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
    if not additions:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE conversations " + ", ".join(additions)))


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
