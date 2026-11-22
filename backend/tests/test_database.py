from datetime import datetime

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import sessionmaker

from app.db import Base, _ensure_conversation_columns, _ensure_message_columns, build_engine, choose_database, mysql_reachable
from app.models import (
    Attachment,
    Conversation,
    McpServer,
    Memory,
    Message,
    Schedule,
    Setting,
    Skill,
    Task,
    UsageEvent,
    User,
)


def test_blank_url_uses_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_SQLITE_PATH", str(tmp_path / "blank.sqlite"))
    url, reason = choose_database("", lambda _url: True)
    assert reason == "unconfigured"
    assert url.startswith("sqlite:///")
    assert url.endswith("blank.sqlite")
    assert (tmp_path / "blank.sqlite").parent.exists()


def test_explicit_sqlite_stays(tmp_path):
    target = tmp_path / "kept.sqlite"
    url, reason = choose_database(f"sqlite:///{target.as_posix()}", lambda _url: False)
    assert reason == "sqlite"
    assert url.endswith("kept.sqlite")


def test_unreachable_mysql_uses_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_SQLITE_PATH", str(tmp_path / "down.sqlite"))
    url, reason = choose_database(
        "mysql+pymysql://harness:harness_local@127.0.0.1:1/harness_missing",
        mysql_reachable,
    )
    assert reason == "fallback"
    assert url.endswith("down.sqlite")


def test_reachable_mysql_stays():
    raw = "mysql+pymysql://harness:secret@127.0.0.1:3306/harness"
    seen = {}

    def probe(url):
        seen["url"] = url
        return True

    url, reason = choose_database(raw, probe)
    assert url == raw
    assert reason == "mysql"
    assert seen["url"] == raw


def test_other_scheme_uses_sqlite_without_probing(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_SQLITE_PATH", str(tmp_path / "other.sqlite"))

    def probe(_url):
        raise AssertionError("非 MySQL 地址不应该尝试连接")

    url, reason = choose_database("postgres://localhost/harness", probe)
    assert reason == "fallback"
    assert url.endswith("other.sqlite")


def test_sqlite_adds_missing_columns_one_table_at_a_time(tmp_path):
    engine = build_engine(f"sqlite:///{(tmp_path / 'migrate.sqlite').as_posix()}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE conversations (id INTEGER PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE messages (id INTEGER PRIMARY KEY)"))
    _ensure_conversation_columns(engine)
    _ensure_message_columns(engine)
    _ensure_conversation_columns(engine)
    conversations = {item["name"] for item in inspect(engine).get_columns("conversations")}
    messages = {item["name"] for item in inspect(engine).get_columns("messages")}
    assert {"pinned", "room_state", "intake_state", "kind"} <= conversations
    assert {"speaker", "channel", "card"} <= messages
    engine.dispose()


def _open(engine):
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    return factory()


def test_sqlite_crud_roundtrip_and_cascade(tmp_path):
    engine = build_engine(f"sqlite:///{(tmp_path / 'crud.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    _ensure_conversation_columns(engine)
    _ensure_message_columns(engine)
    db = _open(engine)
    try:
        user = User(name="用户")
        db.add(user)
        conversation = Conversation(
            title="验收会话",
            provider="deepseek",
            model="deepseek-chat",
            room_state={"current_speaker": "项目经理"},
            intake_state={"round": 1, "choice": "架构"},
        )
        db.add(conversation)
        db.flush()
        message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="先看方案",
            tool_calls=[{"id": "call-1", "name": "read_file"}],
            card={"kind": "choice", "options": ["继续", "停下"]},
            speaker="项目经理",
        )
        db.add(message)
        db.flush()
        attachment = Attachment(
            conversation_id=conversation.id,
            message_id=message.id,
            filename="notes.txt",
            path="notes.txt",
            size=4,
        )
        db.add(attachment)
        db.add(Memory(title="偏好", content="用中文回答"))
        db.add(Task(conversation_id=conversation.id, title="补测试", detail="覆盖 SQLite", status="pending"))
        db.add(Skill(name="review", description="审查", body="看 diff", enabled=True))
        db.add(Setting(name="theme", value="system"))
        db.add(McpServer(name="calc", command="python", args=["calc_server.py"], env={"MODE": "test"}))
        db.add(
            Schedule(
                conversation_id=conversation.id,
                prompt="汇总",
                every_seconds=60,
                next_run=datetime(2026, 11, 22, 8, 0, 0),
                enabled=True,
            )
        )
        db.add(
            UsageEvent(
                conversation_id=conversation.id,
                provider="deepseek",
                model="deepseek-chat",
                prompt_tokens=3,
                completion_tokens=5,
            )
        )
        number_row = Message(conversation_id=conversation.id, role="tool", content="1", tool_calls=1)
        db.add(number_row)
        db.commit()

        conversation.title = "改名了"
        conversation.pinned = True
        conversation.room_state = {"current_speaker": "验收"}
        setting = db.get(Setting, "theme")
        setting.value = "dark"
        db.commit()
        db.refresh(conversation)
        db.refresh(message)
        db.refresh(number_row)

        assert conversation.title == "改名了"
        assert conversation.pinned is True
        assert conversation.room_state == {"current_speaker": "验收"}
        assert conversation.intake_state["choice"] == "架构"
        assert message.tool_calls[0]["name"] == "read_file"
        assert message.card["options"] == ["继续", "停下"]
        assert number_row.tool_calls == 1
        assert db.get(Setting, "theme").value == "dark"
        found = db.scalars(select(Conversation).where(Conversation.title.like("%改名%"))).all()
        assert len(found) == 1
        assert db.scalars(select(McpServer)).one().env == {"MODE": "test"}
        assert db.scalars(select(Skill)).one().enabled is True

        conversation_id = conversation.id
        db.delete(conversation)
        db.commit()
        assert db.get(Conversation, conversation_id) is None
        left = db.scalars(select(Message).where(Message.conversation_id == conversation_id)).all()
        assert left == []
        assert db.scalars(select(Attachment).where(Attachment.conversation_id == conversation_id)).all() == []
        assert db.scalars(select(Memory)).one().title == "偏好"
        db.delete(db.scalars(select(Memory)).one())
        db.commit()
        assert db.scalars(select(Memory)).all() == []
    finally:
        db.close()
        engine.dispose()
