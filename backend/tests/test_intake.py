import json
from pathlib import Path

from app.agent.intake import close_intake
from app.agent.loop import build_model_messages
from app.agent.providers import ScriptedProvider, hub
from app.db import open_session
from app.models import Conversation, Message


def _card(text, question, options):
    return json.dumps({"text": text, "question": question, "options": options}, ensure_ascii=False)


def test_new_chat_asks_twice_then_stops_without_tools(client):
    hub.override = ScriptedProvider(
        steps=[
            {"content": _card("你好，先问一件要紧的事。", "你希望我主要帮你做什么？", ["设计系统架构", "评审方案", "做选型", "拆模块"])},
            {"content": _card("好，先从数学开始。", "你想怎么练？", ["讲一道题", "按知识点练", "做一套卷"])},
            {"content": _card("把题目发过来就行。", "还要再问吗？", ["继续问", "再问一次"])},
        ]
    )
    created = client.post("/api/conversations", json={"title": "高考题解答"}).json()
    started = client.post(f"/api/conversations/{created['id']}/intake")
    assert started.status_code == 200
    first = started.json()["items"][0]
    assert first["content"] == "你好，先问一件要紧的事。"
    assert first["card"]["options"][-1] == "其他"
    assert first["card"]["question"] == "你希望我主要帮你做什么？"
    assert hub.override.calls[0]["tools"] == []

    answered = client.post(
        f"/api/conversations/{created['id']}/intake/answer",
        json={"message_id": first["id"], "choice": "设计系统架构"},
    )
    assert answered.status_code == 200
    cards = [item["card"] for item in answered.json()["items"] if item.get("card")]
    assert cards[0]["choice"] == "设计系统架构"
    assert cards[1]["question"] == "你想怎么练？"
    assert all(item["role"] != "user" for item in answered.json()["items"])

    closed = client.post(
        f"/api/conversations/{created['id']}/intake/answer",
        json={"message_id": cards[1] and answered.json()["items"][-1]["id"], "choice": "讲一道题"},
    )
    assert closed.status_code == 200
    body = closed.json()
    assert body["status"] == "done"
    assert body["items"][-1]["card"] is None
    assert body["items"][-1]["content"] == "把题目发过来就行。"
    again = client.post(
        f"/api/conversations/{created['id']}/intake/answer",
        json={"message_id": first["id"], "choice": "设计系统架构"},
    )
    assert again.status_code == 400

    db = open_session()
    try:
        hidden = db.query(Message).filter(Message.conversation_id == created["id"], Message.channel == "intake").all()
        assert [row.content for row in hidden] == [
            "问题：你希望我主要帮你做什么？\n我的选择：设计系统架构",
            "问题：你想怎么练？\n我的选择：讲一道题",
        ]
        conversation = db.get(Conversation, created["id"])
        packed = build_model_messages(db, conversation, Path(conversation.workspace))
        blob = json.dumps(packed, ensure_ascii=False)
        assert "我的选择：设计系统架构" in blob
        assert "我的选择：讲一道题" in blob
    finally:
        db.close()


def test_broken_opening_still_offers_a_card(client):
    hub.override = ScriptedProvider(steps=[{"content": "不是 JSON"}])
    created = client.post("/api/conversations", json={"title": "技术架构师"}).json()
    started = client.post(f"/api/conversations/{created['id']}/intake").json()
    card = started["items"][0]["card"]
    assert card["options"][-1] == "其他"
    assert len(card["options"]) >= 3


def test_other_choice_keeps_the_typed_text(client):
    hub.override = ScriptedProvider(
        steps=[
            {"content": _card("你好。", "先练哪科？", ["数学", "物理"])},
            {"content": _card("把题目发来。", "", [])},
        ]
    )
    created = client.post("/api/conversations", json={"title": "高考题解答"}).json()
    started = client.post(f"/api/conversations/{created['id']}/intake").json()
    message_id = started["items"][0]["id"]
    answered = client.post(
        f"/api/conversations/{created['id']}/intake/answer",
        json={"message_id": message_id, "choice": "其他", "text": "先讲函数题"},
    )
    assert answered.status_code == 200
    assert answered.json()["items"][0]["card"]["choice"] == "先讲函数题"
    assert answered.json()["status"] == "done"


def test_typing_a_real_message_closes_the_unanswered_card(client, db):
    hub.override = ScriptedProvider(steps=[{"content": _card("你好。", "先做什么？", ["甲", "乙"])}])
    created = client.post("/api/conversations", json={"title": "技术架构师"}).json()
    client.post(f"/api/conversations/{created['id']}/intake")
    conversation = db.get(Conversation, created["id"])
    db.refresh(conversation)
    close_intake(db, conversation)
    db.commit()
    shown = client.get(f"/api/conversations/{created['id']}/messages").json()["items"]
    assert shown[0]["card"] is None
    assert shown[0]["content"] == "你好。"


def test_group_does_not_take_the_opening(client):
    created = client.post(
        "/api/groups",
        json={"title": "一组", "members": [{"name": "项目经理", "duty": "派活"}, {"name": "实现工程师", "duty": "写代码"}]},
    ).json()
    response = client.post(f"/api/conversations/{created['id']}/intake")
    assert response.status_code == 400
