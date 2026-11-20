import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_WORKSPACE = Path(tempfile.mkdtemp(prefix="harness-ws-"))
os.environ["DATABASE_URL"] = (
    "mysql+pymysql://harness:harness_local@127.0.0.1:3306/harness_test?charset=utf8mb4"
)
os.environ["AUTO_SEED"] = "false"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["DEEPSEEK_API_KEY"] = "test-key"
os.environ["DEEPSEEK_BASE_URL"] = "https://api.deepseek.com"
os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:11434"
os.environ["HARNESS_WORKSPACE"] = str(TEST_WORKSPACE)

import pytest
from sqlalchemy import text

from app.agent.loop import turn_tokens
from app.agent.mcp_bridge import clear_snapshot
from app.agent.providers import hub
from app.agent.state import JOBS, PLAN_RETURN, READS
from app.agent.tools import ApprovalBroker, ToolContext, broker
from app.config import reset_settings_cache
from app.db import Base, get_engine, init_db, open_session
from app.services.runtime import ensure_defaults

reset_settings_cache()


@pytest.fixture(autouse=True)
def clean_database():
    init_db()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    db = open_session()
    try:
        ensure_defaults(db)
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


def events_from(raw: str) -> list[dict]:
    import json

    found = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            found.append(json.loads(line[5:].strip()))
    return found
