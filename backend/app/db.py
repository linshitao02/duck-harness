from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None


def get_engine():
    global _engine, _Session
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            pool_recycle=3600,
            pool_size=5,
            max_overflow=10,
            future=True,
        )
        _Session = sessionmaker(
            bind=_engine,
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
    return _engine


def open_session():
    get_engine()
    return _Session()


def init_db() -> None:
    from . import models  # noqa: F401

    engine = get_engine()
    Base.metadata.create_all(engine)
    _ensure_conversation_columns(engine)
    _ensure_message_columns(engine)


def _ensure_conversation_columns(engine) -> None:
    from sqlalchemy import inspect, text

    columns = {item["name"] for item in inspect(engine).get_columns("conversations")}
    additions = []
    if "pinned" not in columns:
        additions.append("ADD COLUMN pinned TINYINT(1) NOT NULL DEFAULT 0")
    if "unread" not in columns:
        additions.append("ADD COLUMN unread TINYINT(1) NOT NULL DEFAULT 0")
    if "hidden" not in columns:
        additions.append("ADD COLUMN hidden TINYINT(1) NOT NULL DEFAULT 0")
    if "group_name" not in columns:
        additions.append("ADD COLUMN group_name VARCHAR(64) NOT NULL DEFAULT ''")
    if "kind" not in columns:
        additions.append("ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'chat'")
    if "room_id" not in columns:
        additions.append("ADD COLUMN room_id INT NULL")
    if "member_name" not in columns:
        additions.append("ADD COLUMN member_name VARCHAR(64) NOT NULL DEFAULT ''")
    if "member_prompt" not in columns:
        additions.append("ADD COLUMN member_prompt TEXT NULL")
    if "room_state" not in columns:
        additions.append("ADD COLUMN room_state JSON NULL")
    if "intake_state" not in columns:
        additions.append("ADD COLUMN intake_state JSON NULL")
    if not additions:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE conversations " + ", ".join(additions)))


def _ensure_message_columns(engine) -> None:
    from sqlalchemy import inspect, text

    columns = {item["name"] for item in inspect(engine).get_columns("messages")}
    additions = []
    if "speaker" not in columns:
        additions.append("ADD COLUMN speaker VARCHAR(64) NOT NULL DEFAULT ''")
    if "channel" not in columns:
        additions.append("ADD COLUMN channel VARCHAR(16) NOT NULL DEFAULT 'public'")
    if "counterpart" not in columns:
        additions.append("ADD COLUMN counterpart VARCHAR(64) NOT NULL DEFAULT ''")
    if "handoff_target" not in columns:
        additions.append("ADD COLUMN handoff_target VARCHAR(64) NOT NULL DEFAULT ''")
    if "card" not in columns:
        additions.append("ADD COLUMN card JSON NULL")
    if not additions:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE messages " + ", ".join(additions)))


@contextmanager
def session_scope():
    db = open_session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
