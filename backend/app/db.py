import logging
import re
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from .config import get_settings, sqlite_file

logger = logging.getLogger("harness.db")

_DETAIL = {
    "mysql": "MySQL 已连接",
    "sqlite": "SQLite 已连接",
    "unconfigured": "SQLite，未配置 MySQL",
    "fallback": "SQLite，MySQL 连不上",
}


class Base(DeclarativeBase):
    pass


_engine = None
_Session = None
_reason = ""


def sqlite_url() -> str:
    path = sqlite_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + path.resolve().as_posix()


def _prepare_sqlite(url: str) -> str:
    if url.endswith(":memory:"):
        return url
    path_text = url.removeprefix("sqlite:///")
    target = Path(path_text)
    target.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + target.resolve().as_posix()


def _looks_like_mysql(url: str) -> bool:
    name = url.split(":", 1)[0].lower()
    return name == "mysql" or name.startswith("mysql+") or name == "mariadb" or name.startswith("mariadb+")


def _safe_error(exc: Exception) -> str:
    line = str(exc).splitlines()[0]
    line = re.sub(r"[a-zA-Z][\w+]*://\S+", "…", line)
    return f"{exc.__class__.__name__}: {line}"[:240]


def mysql_reachable(url: str) -> bool:
    probe = create_engine(
        url,
        pool_pre_ping=True,
        poolclass=NullPool,
        connect_args={"connect_timeout": 1},
        future=True,
    )
    try:
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("MySQL 不可用：%s", _safe_error(exc))
        return False
    finally:
        probe.dispose()


def choose_database(url: str, probe) -> tuple[str, str]:
    raw = (url or "").strip()
    if not raw:
        return sqlite_url(), "unconfigured"
    if raw.lower().startswith("sqlite"):
        return _prepare_sqlite(raw), "sqlite"
    if _looks_like_mysql(raw) and probe(raw):
        return raw, "mysql"
    return sqlite_url(), "fallback"


def _sqlite_on_connect(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def build_engine(url: str):
    if url.startswith("sqlite"):
        engine = create_engine(
            url,
            future=True,
            poolclass=NullPool,
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        event.listen(engine, "connect", _sqlite_on_connect)
        return engine
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=3600,
        pool_size=5,
        max_overflow=10,
        future=True,
    )


def reset_engine() -> None:
    global _engine, _Session, _reason
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None
    _reason = ""


def get_engine():
    global _engine, _Session, _reason
    if _engine is None:
        url, _reason = choose_database(get_settings().database_url, mysql_reachable)
        _engine = build_engine(url)
        _Session = sessionmaker(
            bind=_engine,
            autoflush=False,
            expire_on_commit=False,
            future=True,
        )
        logger.info("数据库：%s", _DETAIL.get(_reason, _reason))
    return _engine


def database_info() -> dict:
    engine = get_engine()
    kind = "mysql" if engine.dialect.name == "mysql" else "sqlite"
    return {
        "database": kind,
        "reason": _reason,
        "detail": _DETAIL.get(_reason, _DETAIL.get(kind, kind)),
    }


def open_session():
    get_engine()
    return _Session()


def init_db() -> None:
    from . import models  # noqa: F401

    engine = get_engine()
    Base.metadata.create_all(engine)
    _ensure_user_columns(engine)
    _ensure_conversation_columns(engine)
    _ensure_message_columns(engine)
    _ensure_owner_columns(engine)
    _ensure_skill_scope(engine)
    _ensure_username_index(engine)


def _ensure_columns(engine, table: str, columns: list[tuple[str, str, str]]) -> None:
    existing = {item["name"] for item in inspect(engine).get_columns(table)}
    dialect = engine.dialect.name
    additions = []
    for name, mysql_ddl, sqlite_ddl in columns:
        if name in existing:
            continue
        ddl = sqlite_ddl if dialect == "sqlite" else mysql_ddl
        additions.append(f"ADD COLUMN {name} {ddl}")
    if not additions:
        return
    with engine.begin() as conn:
        if dialect == "sqlite":
            for clause in additions:
                conn.execute(text(f"ALTER TABLE {table} {clause}"))
        else:
            conn.execute(text(f"ALTER TABLE {table} " + ", ".join(additions)))


def _ensure_user_columns(engine) -> None:
    if "users" not in inspect(engine).get_table_names():
        return
    _ensure_columns(
        engine,
        "users",
        [
            ("username", "VARCHAR(64) NULL", "VARCHAR(64)"),
            ("password_hash", "VARCHAR(255) NOT NULL DEFAULT ''", "VARCHAR(255) NOT NULL DEFAULT ''"),
        ],
    )


def _ensure_owner_columns(engine) -> None:
    column = [("user_id", "INT NULL", "INTEGER")]
    for table in ("conversations", "memories", "tasks", "schedules", "usage_events", "skills", "mcp_servers"):
        if table not in inspect(engine).get_table_names():
            continue
        _ensure_columns(engine, table, column)


def _ensure_username_index(engine) -> None:
    if "users" not in inspect(engine).get_table_names():
        return
    indexes = inspect(engine).get_indexes("users")
    if any(item.get("unique") and item.get("column_names") == ["username"] for item in indexes):
        return
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE UNIQUE INDEX uq_users_username ON users (username)"))
    except Exception:
        logger.info("用户名唯一索引已存在")


def _ensure_skill_scope(engine) -> None:
    if "skills" not in inspect(engine).get_table_names():
        return
    uniques = inspect(engine).get_unique_constraints("skills")
    name_only = [item for item in uniques if item.get("column_names") == ["name"]]
    if not name_only:
        return
    with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            conn.execute(text("ALTER TABLE skills RENAME TO skills_old"))
            conn.execute(
                text(
                    "CREATE TABLE skills ("
                    "id INTEGER PRIMARY KEY, "
                    "user_id INTEGER, "
                    "name VARCHAR(64) NOT NULL, "
                    "description VARCHAR(512) NOT NULL DEFAULT '', "
                    "body TEXT NOT NULL DEFAULT '', "
                    "enabled BOOLEAN NOT NULL DEFAULT 1, "
                    "UNIQUE (user_id, name))"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO skills (id, user_id, name, description, body, enabled) "
                    "SELECT id, user_id, name, description, body, enabled FROM skills_old"
                )
            )
            conn.execute(text("DROP TABLE skills_old"))
            return
        for item in name_only:
            key = item.get("name") or "name"
            conn.execute(text(f"ALTER TABLE skills DROP INDEX {key}"))
        conn.execute(text("CREATE UNIQUE INDEX uq_skill_user_name ON skills (user_id, name)"))


def _ensure_conversation_columns(engine) -> None:
    _ensure_columns(
        engine,
        "conversations",
        [
            ("pinned", "TINYINT(1) NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0"),
            ("unread", "TINYINT(1) NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0"),
            ("hidden", "TINYINT(1) NOT NULL DEFAULT 0", "INTEGER NOT NULL DEFAULT 0"),
            ("group_name", "VARCHAR(64) NOT NULL DEFAULT ''", "VARCHAR(64) NOT NULL DEFAULT ''"),
            ("kind", "VARCHAR(16) NOT NULL DEFAULT 'chat'", "VARCHAR(16) NOT NULL DEFAULT 'chat'"),
            ("room_id", "INT NULL", "INTEGER"),
            ("member_name", "VARCHAR(64) NOT NULL DEFAULT ''", "VARCHAR(64) NOT NULL DEFAULT ''"),
            ("member_prompt", "TEXT NULL", "TEXT"),
            ("room_state", "JSON NULL", "TEXT"),
            ("intake_state", "JSON NULL", "TEXT"),
            ("user_id", "INT NULL", "INTEGER"),
        ],
    )


def _ensure_message_columns(engine) -> None:
    _ensure_columns(
        engine,
        "messages",
        [
            ("speaker", "VARCHAR(64) NOT NULL DEFAULT ''", "VARCHAR(64) NOT NULL DEFAULT ''"),
            ("channel", "VARCHAR(16) NOT NULL DEFAULT 'public'", "VARCHAR(16) NOT NULL DEFAULT 'public'"),
            ("counterpart", "VARCHAR(64) NOT NULL DEFAULT ''", "VARCHAR(64) NOT NULL DEFAULT ''"),
            ("handoff_target", "VARCHAR(64) NOT NULL DEFAULT ''", "VARCHAR(64) NOT NULL DEFAULT ''"),
            ("card", "JSON NULL", "TEXT"),
        ],
    )


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
