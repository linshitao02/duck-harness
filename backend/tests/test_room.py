import asyncio
from pathlib import Path

import pytest

from app.agent.group_run import run_group_turn
from app.agent.room import RoomMessage, SwarmRoom, normalize_max_turns
from app.agent.tools import ToolContext, execute, schemas
from app.db import open_session
from app.models import Conversation
from app.services.runtime import room_members


def test_swarm_follows_the_last_handoff_and_stops_for_the_user():
    room = SwarmRoom(["协调者", "方案设计师", "风险评审员"])
    assert room.start([RoomMessage("user", "做一版方案", target="协调者", kind="handoff")]) == "协调者"
    assert room.observe([RoomMessage("协调者", "请写初稿", target="方案设计师", kind="handoff")]) == "方案设计师"
    assert room.observe([RoomMessage("方案设计师", "初稿好了", target="风险评审员", kind="handoff")]) == "风险评审员"
    assert room.observe([RoomMessage("风险评审员", "可以收", target="user", kind="handoff")]) is None
    assert room.terminated
    assert room.stop_reason == "交接给用户"


def test_old_turn_cap_reads_as_the_current_limit():
    assert normalize_max_turns(None) == 12
    assert normalize_max_turns(6) == 12
    assert normalize_max_turns(4) == 4


def test_missing_handoff_keeps_the_same_speaker_until_max_turns():
    room = SwarmRoom(["方案设计师", "风险评审员"], max_turns=2)
    assert room.start([RoomMessage("user", "开始", target="方案设计师", kind="handoff")]) == "方案设计师"
    assert room.observe([RoomMessage("方案设计师", "还在写")]) == "方案设计师"
    assert room.observe([RoomMessage("方案设计师", "仍在写")]) is None
    assert "Maximum number of turns 2 reached." in room.stop_reason


def test_invalid_handoff_target_is_rejected():
    room = SwarmRoom(["方案设计师", "风险评审员"])
    room.start([RoomMessage("user", "开始", target="方案设计师", kind="handoff")])
    with pytest.raises(ValueError, match="不是成员"):
        room.observe([RoomMessage("方案设计师", "交给外人", target="外人", kind="handoff")])


def test_new_user_handoff_can_resume_after_returning_to_the_user():
    room = SwarmRoom(["协调者", "方案设计师"])
    room.thread = [RoomMessage("方案设计师", "交回", target="user", kind="handoff")]
    with pytest.raises(ValueError):
        room.validate_group_state(None)
    assert room.start([RoomMessage("user", "再来一轮", target="协调者", kind="handoff")]) == "协调者"


def test_handoff_tool_is_hidden_outside_a_room():
    assert all(item["function"]["name"] != "handoff" for item in schemas())
    ctx = ToolContext(
        db=None,
        workspace=Path("."),
        conversation_id=None,
        permission_mode="auto",
        broker=None,
        emit=None,
        provider_name="deepseek",
        model="deepseek-chat",
    )
    refused = asyncio.run(execute("handoff", {"target": "方案设计师"}, ctx))
    assert refused.ok is False
    ctx.room = {"participants": ["方案设计师", "风险评审员"], "speaker": "方案设计师", "handoff": None, "private": []}
    accepted = asyncio.run(execute("handoff", {"target": "风险评审员"}, ctx))
    assert accepted.ok is True
    assert ctx.room["handoff"]["target"] == "风险评审员"
    private = asyncio.run(execute("send_private", {"to": "风险评审员", "text": "只给你看"}, ctx))
    assert private.ok is True
    assert ctx.room["private"][0]["text"] == "只给你看"


def test_group_room_runs_the_handoff_chain_and_hides_private_messages(client):
    created = client.post(
        "/api/groups",
        json={
            "title": "评审群",
            "members": [
                {"name": "协调者", "duty": "派活"},
                {"name": "方案设计师", "duty": "写方案"},
                {"name": "风险评审员", "duty": "找漏洞"},
            ],
        },
    ).json()
    assert created["kind"] == "group"
    listed = client.get("/api/conversations").json()["items"]
    members = [item for item in listed if item["room_id"] == created["id"]]
    assert {item["member_name"] for item in members} == {"协调者", "方案设计师", "风险评审员"}

    plan = {
        "协调者": ("先写方案", {"target": "方案设计师"}, [{"to": "方案设计师", "text": "私下把范围收窄", "source": "协调者"}]),
        "方案设计师": ("初稿在这里", {"target": "风险评审员"}, []),
        "风险评审员": ("有两个高风险", {"target": "user"}, [{"to": "方案设计师", "text": "请按这两点改", "source": "风险评审员"}]),
    }

    async def speak(name, _room, _emit):
        return plan[name]

    async def scenario():
        db = open_session()
        try:
            group = db.get(Conversation, created["id"])
            events = []

            async def emit(event):
                events.append(event)

            await run_group_turn(db, group, "做一版求职助手方案", emit, "room-token", speaker=speak)
            tokens = [event for event in events if event["type"] == "token"]
            assert [event["text"] for event in tokens] == ["先写方案", "初稿在这里", "有两个高风险"]
            assert [event["handoff_target"] for event in tokens] == ["方案设计师", "风险评审员", "user"]
            return [event["name"] for event in events if event["type"] == "speaker"]
        finally:
            db.close()

    assert asyncio.run(scenario()) == ["协调者", "方案设计师", "风险评审员"]
    public = client.get(f"/api/conversations/{created['id']}/messages").json()
    assert [item["speaker"] for item in public["items"] if item["role"] == "assistant"] == ["协调者", "方案设计师", "风险评审员"]
    assert all("私下" not in item["content"] for item in public["items"])
    assert public["private_threads"] == []

    designer = next(item for item in members if item["member_name"] == "方案设计师")
    private = client.get(f"/api/conversations/{designer['id']}/messages").json()
    threads = {item["counterpart"]: item for item in private["private_threads"]}
    assert threads["协调者"]["count"] == 1
    assert threads["风险评审员"]["count"] == 1
    assert "私下把范围收窄" not in " ".join(item["content"] for item in private["items"])
    assert "你好，我已就位" in private["items"][0]["content"]

    removed = client.delete(f"/api/conversations/{created['id']}")
    assert removed.status_code == 200
    left = client.get("/api/conversations").json()["items"]
    assert all(item["id"] != created["id"] and item.get("room_id") != created["id"] for item in left)


def test_at_mention_hands_the_turn_to_that_member(client):
    created = client.post(
        "/api/groups",
        json={
            "title": "派活群",
            "members": [
                {"name": "实现工程师", "duty": "写代码"},
                {"name": "项目经理", "duty": "派活"},
                {"name": "验收工程师", "duty": "跑测试"},
            ],
        },
    ).json()
    seen = []

    async def speak(name, _room, _emit):
        seen.append(name)
        if name == "项目经理":
            return ("请实现加班费", {"target": "实现工程师"}, [])
        if name == "实现工程师":
            return ("代码和测试写好了", {"target": "验收工程师"}, [])
        return ("测试通过", {"target": "user"}, [])

    async def scenario():
        db = open_session()
        try:
            group = db.get(Conversation, created["id"])

            async def emit(_event):
                return None

            await run_group_turn(db, group, "@项目经理 做加班费计算，做完告诉我", emit, "mention-token", speaker=speak)
        finally:
            db.close()

    asyncio.run(scenario())
    assert seen == ["项目经理", "实现工程师", "验收工程师"]
    public = client.get(f"/api/conversations/{created['id']}/messages").json()
    assert public["items"][0]["handoff_target"] == "项目经理"
    assert public["items"][0]["speaker"] == "user"


def test_group_pulls_in_existing_conversations_and_keeps_them_when_deleted(client):
    first = client.post("/api/conversations", json={"title": "项目经理", "icon_key": "star", "icon_color": "#111111"}).json()
    second = client.post("/api/conversations", json={"title": "实现工程师", "icon_key": "circle", "icon_color": "#222222"}).json()
    created = client.post(
        "/api/groups",
        json={
            "title": "加班费群",
            "conversation_ids": [second["id"], first["id"]],
            "icon_key": "talk",
            "icon_color": "#3b82f6",
        },
    ).json()
    assert created["kind"] == "group"
    assert created["icon_key"] == "talk"
    assert created["icon_color"] == "#3b82f6"
    listed = {item["id"]: item for item in client.get("/api/conversations").json()["items"]}
    assert listed[second["id"]]["room_id"] == created["id"]
    assert listed[second["id"]]["member_name"] == "实现工程师"
    assert listed[second["id"]]["kind"] == "chat"
    assert listed[first["id"]]["member_name"] == "项目经理"
    assert not any(item.get("kind") == "member" and item.get("room_id") == created["id"] for item in listed.values())

    db = open_session()
    try:
        group = db.get(Conversation, created["id"])
        assert group.room_state["member_ids"] == [second["id"], first["id"]]
        assert group.room_state["current_speaker"] == "实现工程师"
    finally:
        db.close()

    removed = client.delete(f"/api/conversations/{created['id']}")
    assert removed.status_code == 200
    left = {item["id"]: item for item in client.get("/api/conversations").json()["items"]}
    assert created["id"] not in left
    assert left[first["id"]]["title"] == "项目经理"
    assert left[first["id"]]["room_id"] is None
    assert left[first["id"]]["member_name"] == ""
    assert left[second["id"]]["room_id"] is None


def test_chat_can_join_a_second_group(client):
    manager = client.post("/api/conversations", json={"title": "项目经理"}).json()
    builder = client.post("/api/conversations", json={"title": "实现工程师"}).json()
    reviewer = client.post("/api/conversations", json={"title": "验收工程师"}).json()
    first = client.post(
        "/api/groups",
        json={"title": "加班费群", "conversation_ids": [builder["id"], manager["id"]]},
    ).json()
    second = client.post(
        "/api/groups",
        json={"title": "财务-加班费计算", "conversation_ids": [builder["id"], reviewer["id"]]},
    ).json()
    assert second["member_ids"] == [builder["id"], reviewer["id"]]
    listed = {item["id"]: item for item in client.get("/api/conversations").json()["items"]}
    assert listed[builder["id"]]["room_id"] == first["id"]
    assert set(listed[builder["id"]]["room_ids"]) == {first["id"], second["id"]}
    assert listed[builder["id"]]["member_name"] == "实现工程师"
    assert listed[manager["id"]]["room_ids"] == [first["id"]]

    db = open_session()
    try:
        group = db.get(Conversation, second["id"])
        assert [row.member_name for row in room_members(db, group)] == ["实现工程师", "验收工程师"]
    finally:
        db.close()

    assert client.delete(f"/api/conversations/{first['id']}").status_code == 200
    left = {item["id"]: item for item in client.get("/api/conversations").json()["items"]}
    assert left[builder["id"]]["room_id"] == second["id"]
    assert left[builder["id"]]["member_name"] == "实现工程师"
    assert left[builder["id"]]["room_ids"] == [second["id"]]
    assert left[manager["id"]]["room_id"] is None

    assert client.delete(f"/api/conversations/{second['id']}").status_code == 200
    left = {item["id"]: item for item in client.get("/api/conversations").json()["items"]}
    assert left[builder["id"]]["room_id"] is None
    assert left[builder["id"]]["member_name"] == ""
    assert left[reviewer["id"]]["room_id"] is None


def test_group_rejects_a_single_existing_conversation(client):
    only = client.post("/api/conversations", json={"title": "只有一个"}).json()
    response = client.post("/api/groups", json={"conversation_ids": [only["id"]]})
    assert response.status_code == 400


def test_group_requires_two_members(client):
    response = client.post("/api/groups", json={"members": [{"name": "只有一个", "duty": ""}]})
    assert response.status_code == 400
