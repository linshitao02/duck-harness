"""新建普通会话的开场问答。不进入工具循环，最多两轮选项。"""

import json
import logging
import re
from datetime import datetime

from sqlalchemy import func, select

from ..models import Conversation, Message
from ..services.runtime import present_messages
from .providers import hub

logger = logging.getLogger("harness.intake")

OTHER = "其他"
FALLBACK_OPTIONS = ["先讲清楚要做的事", "先看已有材料", "先拆成步骤", "先给一个做法", OTHER]


def close_intake(db, conversation: Conversation) -> None:
    """用户开始真正打字时，未答完的开场题收起，不再接受点选。"""
    state = dict(conversation.intake_state or {})
    if state.get("status") != "asking":
        return
    pending_id = state.get("pending_id")
    if pending_id:
        row = db.get(Message, int(pending_id))
        if row is not None and row.conversation_id == conversation.id and isinstance(row.card, dict) and not row.card.get("choice"):
            row.card = None
    conversation.intake_state = {"status": "done", "round": int(state.get("round") or 0), "pending_id": None}


def _parse(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, count=1).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("开场没有返回 JSON")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("开场没有返回 JSON")
    return data


def _options(raw) -> list[str]:
    found = []
    for item in raw or []:
        label = str(item.get("label") if isinstance(item, dict) else item).strip()
        if not label or label == OTHER or label in found:
            continue
        found.append(label[:40])
        if len(found) == 5:
            break
    return found


def _card_from(data: dict, *, force_question: bool, forbid_question: bool, title: str) -> tuple[str, dict | None]:
    text = str(data.get("text") or "").strip()
    question = "" if forbid_question else str(data.get("question") or "").strip()
    options = [] if forbid_question else _options(data.get("options"))
    if force_question and (not question or len(options) < 2):
        text = text or f"你好，我是{title}。先确认一件最要紧的事。"
        question = "你希望我主要帮你做什么？"
        options = [item for item in FALLBACK_OPTIONS if item != OTHER]
    if question and len(options) >= 2:
        card = {"question": question[:80], "options": options + [OTHER], "choice": ""}
        return text or f"你好，我是{title}。", card
    if not text:
        text = "可以了。你直接把要做的事发过来，我按刚才的选择开始。"
    return text, None


def _prompt(conversation: Conversation, rows: list[Message], *, forbid_question: bool) -> list[dict]:
    history = []
    for row in rows:
        if row.role == "assistant":
            history.append(f"助手：{row.content or ''}")
            question = (row.card or {}).get("question") if isinstance(row.card, dict) else ""
            if question:
                history.append(f"问题：{question}")
        elif (row.channel or "") == "intake":
            history.append(f"用户：{row.content or ''}")
    follow = "不要再提问，只写一句收束，请对方把具体任务发过来。" if forbid_question else (
        "如果还缺一个关键信息，就再问一个更具体的问题，并给 4 个具体选项。"
        "如果已经够用来开始，就只写一句收束，question 留空，options 留空。"
    )
    asked = "\n".join(history) if history else "（还没有）"
    return [
        {
            "role": "system",
            "content": (
                "你在为一个新会话做开场，不能调用工具，也不能开始做任务。"
                "只根据会话名字问清楚用户想做什么。最后一项「其他」由程序补上，你不要写。"
                "只返回一个 JSON 对象，不要 Markdown："
                '{"text":"一两句中文","question":"一个问题或空字符串","options":["选项"]}'
            ),
        },
        {
            "role": "user",
            "content": (
                f"会话名字：{conversation.title}\n"
                f"已经发生的开场：\n{asked}\n\n{follow}"
            ),
        },
    ]


async def _complete(db, conversation: Conversation, messages: list[dict]) -> str:
    provider = hub.resolve(conversation.provider, db)
    parts: list[str] = []
    async for event in provider.stream(
        messages,
        [],
        conversation.model or "deepseek-chat",
        max_tokens=700,
        query_source="foreground",
    ):
        if event.kind == "text":
            parts.append(event.text)
    return "".join(parts)


def _rows(db, conversation_id: int) -> list[Message]:
    return db.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id.asc())
    ).all()


def _save_assistant(db, conversation: Conversation, text: str, card: dict | None) -> Message:
    row = Message(
        conversation_id=conversation.id,
        role="assistant",
        content=text,
        status="done",
        card=card,
    )
    db.add(row)
    db.flush()
    conversation.preview = text.replace("\n", " ")[:120]
    conversation.updated_at = datetime.now()
    return row


async def start_intake(db, conversation: Conversation) -> dict:
    if (conversation.kind or "chat") != "chat" or conversation.room_id:
        raise ValueError("只有新建的普通会话才做开场问答")
    existing = db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == conversation.id))
    if existing:
        raise ValueError("这个会话已经开始了")
    raw = ""
    try:
        raw = await _complete(db, conversation, _prompt(conversation, [], forbid_question=False))
        data = _parse(raw)
    except Exception:
        logger.exception("开场生成失败")
        data = {}
    text, card = _card_from(data, force_question=True, forbid_question=False, title=conversation.title or "新会话")
    row = _save_assistant(db, conversation, text, card)
    conversation.intake_state = {
        "status": "asking" if card else "done",
        "round": 1 if card else 0,
        "pending_id": row.id if card else None,
    }
    db.commit()
    return {"items": present_messages(db, conversation.id), "status": conversation.intake_state["status"]}


async def answer_intake(db, conversation: Conversation, message_id: int, choice: str, text: str = "") -> dict:
    state = dict(conversation.intake_state or {})
    if state.get("status") != "asking":
        raise ValueError("开场已经结束")
    if int(state.get("pending_id") or 0) != int(message_id):
        raise ValueError("这道题已经答过了")
    row = db.get(Message, int(message_id))
    if row is None or row.conversation_id != conversation.id or not isinstance(row.card, dict):
        raise ValueError("找不到这道题")
    options = [str(item) for item in row.card.get("options") or []]
    picked = (choice or "").strip()
    note = (text or "").strip()
    if picked == OTHER:
        picked = note or OTHER
    elif picked not in options:
        if OTHER not in options or not picked:
            raise ValueError("没有这个选项")
    card = dict(row.card)
    card["choice"] = picked
    row.card = card
    question = str(card.get("question") or "")
    db.add(
        Message(
            conversation_id=conversation.id,
            role="user",
            content=f"问题：{question}\n我的选择：{picked}",
            status="done",
            channel="intake",
        )
    )
    db.flush()
    round_no = int(state.get("round") or 1)
    forbid = round_no >= 2
    try:
        raw = await _complete(db, conversation, _prompt(conversation, _rows(db, conversation.id), forbid_question=forbid))
        data = _parse(raw)
    except Exception:
        logger.exception("开场追问失败")
        data = {}
    reply, next_card = _card_from(
        data,
        force_question=False,
        forbid_question=forbid,
        title=conversation.title or "新会话",
    )
    saved = _save_assistant(db, conversation, reply, next_card)
    if next_card:
        conversation.intake_state = {"status": "asking", "round": round_no + 1, "pending_id": saved.id}
    else:
        conversation.intake_state = {"status": "done", "round": round_no, "pending_id": None}
    db.commit()
    return {"items": present_messages(db, conversation.id), "status": conversation.intake_state["status"]}
