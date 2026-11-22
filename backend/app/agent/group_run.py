"""把一次用户发言推进群聊房间。工具循环只通过 run_loop 调用，不改它的内部步骤。"""

import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from ..models import Conversation, Message
from ..services.runtime import all_settings, room_members
from .loop import _stopped, run_loop, turn_tokens
from .providers import hub
from .room import USER, RoomMessage, SwarmRoom, normalize_max_turns
from .tools import ToolContext, broker, group_tool_names

logger = logging.getLogger("harness.group")


def _remember_room(conversation: Conversation, room: SwarmRoom) -> None:
    state = dict(conversation.room_state or {})
    state.update(room.state())
    conversation.room_state = state


def load_room(conversation: Conversation, members: list[Conversation]) -> SwarmRoom:
    state = conversation.room_state or {}
    names = [row.member_name for row in members]
    room = SwarmRoom(
        names,
        max_turns=normalize_max_turns(state.get("max_turns")),
        current_speaker=str(state.get("current_speaker") or names[0]),
        current_turn=int(state.get("current_turn") or 0),
    )
    return room


def thread_from_rows(rows: list[Message]) -> list[RoomMessage]:
    thread = []
    for row in rows:
        if (row.channel or "public") == "private" or row.role == "tool":
            continue
        target = row.handoff_target or ""
        kind = "handoff" if target else "text"
        source = row.speaker or ("user" if row.role == "user" else "")
        thread.append(RoomMessage(source, row.content or "", target=target, kind=kind))
    return thread


def addressed_member(content: str, names: list[str]) -> str | None:
    """用户用 @名字 指定这一轮交给谁。多个时取文中最先出现的，同位置取更长的名字。"""
    found: list[tuple[int, int, str]] = []
    for name in names:
        if not name:
            continue
        for mark in (f"@{name}", f"＠{name}"):
            at = content.find(mark)
            if at >= 0:
                found.append((at, -len(name), name))
    if not found:
        return None
    found.sort()
    return found[0][2]


def _member_system(member: Conversation, names: list[str]) -> str:
    others = "、".join(names)
    return (
        f"{member.member_prompt}\n"
        f"群成员：{others}。用户不在这个名单里，交回用户时 target 填 user。\n"
        "你正在群聊里发言。房间里的其他人只能看到你最后的正文，看不到工具过程。\n"
        "做完你这一段后，由你判断下一位，并调用一次 handoff。target 填成员原名或 user，不要带 @。\n"
        "往下走：你这份结果已经够下一位接着做，把 target 写成这位成员。\n"
        "往回退：交给你的结果还达不到要求。退给应该修改的那位成员，正文写明差在哪里。\n"
        "交给用户：整件事已经做完，或者只有用户能做这个决定。\n"
        "只选一个 target。按照当前结果判断，不要默认某个角色永远交给谁，也不要替别人做他们的工作。\n"
        "交给成员时，把 @ 写进句子里、紧挨着你点名的那个人，例如「交给@名字，请补上缺少的部分」。"
        "不要在整段写完后另起一行单独 @。交给用户时正文不必写 @。\n"
        "正文要带上下一位需要的结果：你做了什么、结论是什么、还有什么没完成。不要只写已交给谁。\n"
        "只在正文里写 @名字不会让对方发言，只有 handoff 的 target 才算数。\n"
        "有些话只该一个成员看到、不该留在群记录里时，调用 send_private。to 填那位成员的原名，text 写私聊正文。\n"
        "私聊不会让对方开口，也不能代替 handoff。群里都要知道的派活、结果和退回原因，写在正文里。"
    )


def _transcript(room: SwarmRoom, member_name: str, private_rows: list[Message]) -> str:
    lines = []
    for message in room.thread:
        body = message.content or ""
        if message.kind == "handoff" and message.target and message.target != USER:
            lines.append(f"{message.source}（交给 {message.target}）: {body}")
        else:
            lines.append(f"{message.source}: {body}")
    private_lines = []
    for row in private_rows:
        if member_name not in {row.speaker, row.counterpart}:
            continue
        private_lines.append(f"{row.speaker} 私下对 {row.counterpart}: {row.content or ''}")
    text = "群聊记录：\n" + ("\n".join(lines) if lines else "（还没有）")
    if private_lines:
        text += "\n\n只你能看到的私聊：\n" + "\n".join(private_lines)
    text += "\n\n轮到你发言。说完调用 handoff。"
    return text


async def _speak_with_loop(db, conversation, member, room: SwarmRoom, emit, token: str):
    stored = all_settings(db)
    workspace = Path(conversation.workspace or stored["workspace"])
    provider = hub.resolve(conversation.provider, db)
    private_rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id, Message.channel == "private")
        .order_by(Message.id.asc())
    ).all()
    bag = {
        "speaker": member.member_name,
        "participants": list(room.participants),
        "handoff": None,
        "private": [],
    }
    ctx = ToolContext(
        db=db,
        workspace=workspace,
        conversation_id=conversation.id,
        permission_mode=conversation.permission_mode,
        broker=broker,
        emit=emit,
        provider_name=conversation.provider,
        model=conversation.model,
        turn_token=token,
        conversation_key=conversation.id,
        origin_workspace=workspace,
        room=bag,
    )
    messages = [
        {"role": "system", "content": _member_system(member, room.participants)},
        {"role": "user", "content": _transcript(room, member.member_name, private_rows)},
    ]
    final = await run_loop(messages, provider, ctx, max_steps=8, allowed=group_tool_names())
    if not bag.get("handoff"):
        messages.append(
            {
                "role": "user",
                "content": (
                    "你这一轮还没有调用 handoff。请立刻调用一次。"
                    "由你判断 target：结果够下一位接着做，就填那位成员的原名；还达不到要求，就退给应该修改的成员；整件事做完或只有用户能决定，就填 user。"
                    "target 不要带 @。交给成员时，正文句子里要有 @加这个原名。"
                ),
            }
        )
        nudged = await run_loop(messages, provider, ctx, max_steps=2, allowed=group_tool_names())
        if not (final or "").strip() and nudged:
            final = nudged
    return final or "", bag.get("handoff"), list(bag.get("private") or [])


def _save_public(db, conversation, source: str, content: str, role: str, target: str = "") -> Message:
    row = Message(
        conversation_id=conversation.id,
        role=role,
        content=content,
        speaker=source,
        status="done",
        handoff_target=target,
        channel="public",
    )
    db.add(row)
    return row


def _save_private(db, conversation, source: str, target: str, content: str) -> None:
    db.add(
        Message(
            conversation_id=conversation.id,
            role="assistant",
            content=content,
            speaker=source,
            counterpart=target,
            channel="private",
            status="done",
            in_context=False,
        )
    )


async def run_group_turn(db, conversation, content: str, emit, token: str, speaker=None) -> None:
    content = (content or "").strip()
    if not content:
        await emit({"type": "error", "message": "消息是空的"})
        return
    turn_tokens[conversation.id] = token
    stored = all_settings(db)
    workspace = Path(conversation.workspace or stored["workspace"])
    if not workspace.exists():
        await emit({"type": "error", "message": f"工作区不存在: {workspace}"})
        return
    members = room_members(db, conversation)
    if len(members) < 2:
        await emit({"type": "error", "message": "这个群还没有足够的成员"})
        return
    by_name = {row.member_name: row for row in members}
    room = load_room(conversation, members)
    room.thread = thread_from_rows(
        db.scalars(
            select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id.asc())
        ).all()
    )
    target_name = addressed_member(content, room.participants) or room.participants[0]
    opening = RoomMessage(USER, content, target=target_name, kind="handoff")
    _save_public(db, conversation, USER, content, "user", target=target_name)
    conversation.preview = content.replace("\n", " ")[:120]
    conversation.updated_at = datetime.now()
    db.commit()
    await emit({"type": "user", "content": content})
    try:
        nxt = room.start([opening])
    except ValueError as exc:
        await emit({"type": "error", "message": str(exc)})
        return
    speak = speaker or _speak_with_loop
    while nxt:
        if _stopped(conversation.id, token):
            break
        member = by_name.get(nxt)
        if member is None:
            await emit({"type": "error", "message": f"找不到成员 {nxt}"})
            break
        await emit({"type": "speaker", "name": nxt})
        try:
            if speaker is None:
                text, handoff, privates = await _speak_with_loop(db, conversation, member, room, emit, token)
            else:
                text, handoff, privates = await speaker(nxt, room, emit)
        except Exception as exc:
            logger.exception("群成员发言失败")
            await emit({"type": "error", "message": str(exc)})
            break
        if _stopped(conversation.id, token):
            break
        target = str((handoff or {}).get("target") or "")
        if target == "用户":
            target = USER
        spoken = (text or "").strip() or str((handoff or {}).get("content") or "").strip()
        if not target:
            target = USER
            if not spoken:
                spoken = "这一轮先交回给你。"
        _save_public(db, conversation, nxt, spoken, "assistant", target=target)
        for item in privates or []:
            _save_private(db, conversation, str(item.get("source") or nxt), str(item.get("to") or ""), str(item.get("text") or ""))
        conversation.preview = spoken.replace("\n", " ")[:120]
        conversation.updated_at = datetime.now()
        published = RoomMessage(nxt, spoken, target=target, kind="handoff" if target else "text")
        try:
            nxt = room.observe([published])
        except ValueError as exc:
            _remember_room(conversation, room)
            db.commit()
            await emit({"type": "error", "message": str(exc)})
            return
        _remember_room(conversation, room)
        db.commit()
        await emit({"type": "token", "text": spoken, "speaker": published.source})
        if nxt == published.source and target == published.source:
            break
    _remember_room(conversation, room)
    db.commit()
    await emit({"type": "done", "content": conversation.preview or ""})


async def iter_group_events(db, conversation, content: str):
    import asyncio

    token = __import__("uuid").uuid4().hex
    turn_tokens[conversation.id] = token
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event):
        await queue.put(event)

    async def work():
        try:
            await run_group_turn(db, conversation, content, emit, token)
        except Exception as exc:
            logger.exception("group turn crashed")
            await queue.put({"type": "error", "message": str(exc)})
        finally:
            await queue.put(None)

    task = asyncio.create_task(work())
    completed = False
    try:
        while True:
            item = await queue.get()
            if item is None:
                completed = True
                break
            yield item
    finally:
        if not completed and turn_tokens.get(conversation.id) == token:
            turn_tokens[conversation.id] = "stop"
        if not task.done():
            if not completed:
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
