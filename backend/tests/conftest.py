import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_WORKSPACE = Path(tempfile.mkdtemp(prefix="harness-ws-"))
TEST_UPLOADS = Path(tempfile.mkdtemp(prefix="harness-uploads-"))
TEST_SQLITE = Path(tempfile.mkdtemp(prefix="harness-sqlite-"))
MYSQL_TEST_URL = "mysql+pymysql://harness:harness_local@127.0.0.1:3306/harness_test?charset=utf8mb4"
os.environ["DATABASE_URL"] = os.environ.get("HARNESS_TEST_DATABASE_URL") or MYSQL_TEST_URL
os.environ["HARNESS_SQLITE_PATH"] = str(TEST_SQLITE / "fallback.sqlite")
os.environ["AUTO_SEED"] = "false"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["DEEPSEEK_API_KEY"] = "test-key"
os.environ["HARNESS_SECRET_KEY"] = "test-secret-key"
os.environ["DEEPSEEK_BASE_URL"] = "https://api.deepseek.com"
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
os.environ["HARNESS_WORKSPACE"] = str(TEST_WORKSPACE)
os.environ["HARNESS_UPLOAD_DIR"] = str(TEST_UPLOADS)

import pytest
from sqlalchemy import event, text

from app.access import clear_actor
from app.agent.loop import turn_tokens
from app.agent.mcp_bridge import clear_snapshot
from app.agent.providers import hub
from app.agent.state import JOBS, PLAN_RETURN, READS
from app.agent.tools import ApprovalBroker, ToolContext, broker
from app.config import reset_settings_cache

reset_settings_cache()

from app.auth import hash_password
from app.db import Base, database_info, get_engine, init_db, open_session
from app.models import Conversation, McpServer, Memory, Schedule, Skill, Task, UsageEvent, User
from app.services.runtime import ensure_defaults, ensure_user_settings


def _assign_test_owner(_mapper, _connection, target):
    if getattr(target, "user_id", None) is None:
        target.user_id = 1


for _model in (Conversation, Memory, Task, Schedule, UsageEvent, Skill, McpServer):
    event.listen(_model, "before_insert", _assign_test_owner)


@pytest.fixture(autouse=True)
def clean_database():
    init_db()
    engine = get_engine()
    with engine.begin() as conn:
        if engine.dialect.name == "mysql":
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        if engine.dialect.name == "mysql":
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    clear_actor()
    db = open_session()
    try:
        ensure_defaults(db)
        user = db.get(User, 1)
        user.username = "tester"
        user.password_hash = hash_password("tester-pass")
        user.name = "用户"
        db.commit()
        ensure_user_settings(db, user)
    finally:
        db.close()
    hub.override = None
    broker.pending.clear()
    turn_tokens.clear()
    READS.clear()
    JOBS.clear()
    PLAN_RETURN.clear()
    clear_snapshot()
    from app.agent.lsp_client import discard_lsp
    from app.agent.shell_session import discard_shells

    discard_shells()
    discard_lsp()
    yield
    clear_snapshot()
    discard_shells()
    discard_lsp()
    hub.override = None
    broker.pending.clear()
    turn_tokens.clear()
    READS.clear()
    JOBS.clear()
    PLAN_RETURN.clear()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        logged = test_client.post(
            "/api/auth/login",
            json={"username": "tester", "password": "tester-pass"},
        )
        assert logged.status_code == 200, logged.text
        yield test_client


@pytest.fixture
def db():
    session = open_session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def ctx(db, tmp_path):
    async def emit(_event):
        return None

    context = ToolContext(
        db=db,
        workspace=tmp_path,
        conversation_id=None,
        permission_mode="auto",
        broker=broker,
        emit=emit,
        provider_name="deepseek",
        model="deepseek-chat",
        depth=0,
        hub=hub,
    )
    return context


def pytest_report_header(config):
    info = database_info()
    return f"database: {info['database']} ({info['reason']}) {info['detail']}"


def events_from(raw: str) -> list[dict]:
    import json

    found = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            found.append(json.loads(line[5:].strip()))
    return found
