import asyncio
import base64
import json
import logging
import uuid
from pathlib import Path

from ..models import Attachment, Message, UsageEvent
from ..services.cost import estimate_cost
from .agents import catalog
from .context import workspace_brief
from .mcp_bridge import prompt_text, refresh
from .diffutil import split_embedded_diff
from .prompt import system_prompt
from .providers import ModelFallback, hub
from .shrink import shrink_tool_results
from .state import jobs_pending, take_job_notices
from .tools import ToolContext, broker, execute, load_skills, plan_filename, schemas

PARALLEL_TOOLS = {"read_file", "glob_files", "grep", "list_dir", "git_summary", "system_info", "web_search", "web_fetch"}
# 主对话一直转到模型不再调用工具、停止钩子放行，或用户中断。步数只限制显式传入 max_steps 的子代理。
LENGTH_RECOVERIES = 3
CONTEXT_RECOVERIES = 2
MEDIA_RECOVERIES = 1
COMPACT_CHARS = 80_000
# 压缩阈值按字符计。附回的文件必须留在阈值下面，避免刚附回就再次压缩。
POST_COMPACT_FILES = 5
POST_COMPACT_CHARS_PER_FILE = 8_000
POST_COMPACT_CHARS_BUDGET = 24_000
POST_COMPACT_HEADROOM = 60_000
FILE_MUTATIONS = {"write_file", "edit_file", "notebook_edit"}
LENGTH_NUDGE = (
    "输出长度到顶了。从断点直接接着写，不要道歉，也不要复述刚才在做什么。"
    "如果正好断在半句话，就从半句接上。剩下的工作拆成更小的几段。"
)

logger = logging.getLogger("harness.loop")

turn_tokens: dict[int, str] = {}
_mailbox: dict[str, list[str]] = {}


def _keeps_reasoning(model: str) -> bool:
    """思考模型要求把上一步的思考带回下一次请求。普通对话模型不收这个字段。"""
    name = (model or "").lower()
    return "reasoner" in name or "thinking" in name


def _assistant_payload(content: str, model: str, reasoning: str = "", tool_calls: list | None = None) -> dict:
    message = {"role": "assistant", "content": content or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    if reasoning and reasoning.strip() and _keeps_reasoning(model):
        message["reasoning_content"] = reasoning
    return message


def _tool_content(result, model: str = ""):
    text = result.text or "（无输出）"
    image = getattr(result, "image", None)
    if not image or not _supports_vision(model):
        return text
    return [
        {"type": "text", "text": text},
        {
            "type": "image_url",
            "image_url": {"url": f"data:{image['media_type']};base64,{image['data']}"},
        },
    ]


def _push_notice(messages: list[dict], note: str) -> None:
    messages.append(
        {
            "role": "user",
            "content": "后台任务已经结束。根据这些结果继续；如果该做的都做完了，就给出最终答复，不要再重复已经完成的步骤。\n"
            + note,
        }
    )


async def _wait_background(ctx: ToolContext) -> str:
    waited = 0.0
    while jobs_pending(ctx) and waited < 600:
        if ctx.conversation_key and _stopped(ctx.conversation_key, ctx.turn_token):
            break
        await asyncio.sleep(0.3)
        waited += 0.3
    return take_job_notices(ctx)


def send_mail(target: str, text: str) -> None:
    _mailbox.setdefault(str(target), []).append(text)


def _take_mail(target: str) -> str:
    notes = _mailbox.pop(str(target), [])
    return "\n".join(notes).strip()


def _context_overflow(message: str) -> bool:
    text = (message or "").lower()
    return any(
        piece in text
        for piece in (
            "context length",
            "context_length",
            "maximum context",
            "context window",
            "prompt is too long",
            "too many tokens",
            "reduce the length",
        )
    )


def _media_rejected(message: str) -> bool:
    text = (message or "").lower()
    return any(
        piece in text
        for piece in (
            "image exceeds",
            "image too large",
            "too many images",
            "could not process image",
            "invalid image",
            "max image size",
            "pdf too large",
        )
    )


def _strip_media(messages: list[dict]) -> bool:
    """去掉请求里的图片。媒体过大时先抽掉再试一次，不再整段压缩。"""
    removed = False
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        kept = []
        dropped = False
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                dropped = True
                continue
            kept.append(part)
        if not dropped:
            continue
        removed = True
        text = " ".join(
            str(part.get("text") or "") for part in kept if isinstance(part, dict) and part.get("type") == "text"
        ).strip()
        message["content"] = (text + "\n（图片或文档过大，已从这次请求里去掉）").strip()
    return removed


def cancel_turn(conversation_id: int) -> None:
    turn_tokens[conversation_id] = "stop"


def _stopped(conversation_id: int, token: str) -> bool:
    return turn_tokens.get(conversation_id) != token


def _parse_arguments(raw: str):
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    if isinstance(value, dict):
        return value
    return {"value": value}


def _supports_vision(model: str) -> bool:
    name = (model or "").lower()
    return any(token in name for token in ("vl", "vision", "gpt-4o", "gemini"))


def _user_content(db, conversation, row: Message):
    content = row.content or ""
    if not _supports_vision(conversation.model):
        return content
    from sqlalchemy import select

    images = []
    attachments = db.scalars(select(Attachment).where(Attachment.message_id == row.id)).all()
    for item in attachments:
        suffix = Path(item.filename).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            continue
        try:
            raw = Path(item.path).read_bytes()
        except OSError:
            continue
        if len(raw) > 4_000_000:
            continue
        mime = item.mime if str(item.mime).startswith("image/") else "image/png"
        encoded = base64.b64encode(raw).decode("ascii")
        images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
    if not images:
        return content
    return [{"type": "text", "text": content}, *images]


async def _drive(
    provider,
    messages,
    tool_schemas,
    model,
    ctx,
    should_stop,
    on_text=None,
    on_reasoning=None,
    on_start=None,
    query_source="foreground",
    fallback_model="",
):
    """边收模型输出边启动已经解析完的工具。"""
    from .executor import StreamingToolExecutor

    executor = StreamingToolExecutor(ctx, on_start=on_start)
    text = ""
    reasoning = ""
    finish = ""
    prompt = completion = cache_hit = cache_miss = 0
    async for event in provider.stream(
        messages,
        tool_schemas,
        model,
        should_stop=should_stop,
        query_source=query_source,
        fallback_model=fallback_model,
    ):
        if event.kind == "text":
            text += event.text
            if on_text:
                await on_text(event.text)
        elif event.kind == "reasoning":
            reasoning += event.text
            if on_reasoning:
                await on_reasoning(event.text)
        elif event.kind == "tool_call":
            await executor.add(
                {
                    "id": event.tool_id,
                    "name": event.tool_name,
                    "arguments": _parse_arguments(event.arguments_delta),
                }
            )
        elif event.kind == "finish":
            finish = event.text
        elif event.kind == "usage":
            prompt += event.prompt_tokens
            completion += event.completion_tokens
            cache_hit += event.cache_hit_tokens
            cache_miss += event.cache_miss_tokens
        if should_stop and should_stop():
            break
    results = await executor.finish()
    return {
        "text": text,
        "reasoning": reasoning,
        "finish": finish,
        "calls": executor.items,
        "results": results,
        "prompt": prompt,
        "completion": completion,
        "cache_hit": cache_hit,
        "cache_miss": cache_miss,
    }


async def _run_batch(items: list[dict], ctx: ToolContext, before=None) -> list:
    async def run_one(item):
        if before:
            await before(item)
        return await execute(item["name"], item["arguments"], ctx)

    parallel = len(items) > 1 and all(item["name"] in PARALLEL_TOOLS for item in items)
    if parallel:
        return list(await asyncio.gather(*(run_one(item) for item in items)))
    results = []
    for item in items:
        if ctx.conversation_key and _stopped(ctx.conversation_key, ctx.turn_token):
            break
        results.append(await run_one(item))
    return results


async def _remember_session(conversation, messages: list[dict], workspace: Path, provider) -> None:
    """模型这一步已经写进 messages 之后再抽会话笔记。下一步压缩才能用到刚写完的内容。"""
    from .memory import maintain_session_memory

    try:
        await maintain_session_memory(
            workspace,
            conversation.id,
            messages,
            provider,
            conversation.model,
            conversation.summary or "",
        )
    except Exception:
        logger.exception("更新会话笔记失败")


def _message_size(messages: list[dict]) -> int:
    return sum(len(json.dumps(item, ensure_ascii=False, default=str)) for item in messages)


def restored_file_note(kept_texts: list[str], occupied: int) -> str:
    """压缩后把最近读过、尾部没有留下的文件按磁盘当前内容附回去。"""
    from .edits import path_key
    from .state import READS

    room = min(POST_COMPACT_CHARS_BUDGET, POST_COMPACT_HEADROOM - occupied)
    if room < 500:
        return ""
    candidates = []
    for key, stamp in READS.items():
        if stamp.get("partial"):
            continue
        path = Path(key)
        if not path.is_file():
            continue
        if any(key in text or str(path) in text for text in kept_texts):
            continue
        candidates.append((float(stamp.get("read_at") or stamp.get("mtime") or 0), path))
    candidates.sort(key=lambda item: item[0], reverse=True)
    chunks = []
    used = 0
    for _stamp, path in candidates[:POST_COMPACT_FILES]:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = raw.splitlines()
        numbered = "\n".join(f"{index}|{line}" for index, line in enumerate(lines, 1))
        piece = f"{path_key(path)} 共 {len(lines)} 行。这是压缩后附回的当前内容。\n{numbered}"
        remaining = room - used
        if remaining < 200:
            break
        if len(piece) > min(POST_COMPACT_CHARS_PER_FILE, remaining):
            piece = piece[: min(POST_COMPACT_CHARS_PER_FILE, remaining)] + "\n…已截断"
        chunks.append(piece)
        used += len(piece)
    if not chunks:
        return ""
    return "压缩后重新附上最近读过、尾部没有保留的文件。内容来自磁盘上的当前版本。\n\n" + "\n\n".join(chunks)


def _with_restored_files(messages: list[dict]) -> list[dict]:
    kept = [str(item.get("content") or "") for item in messages if item.get("role") != "system"]
    note = restored_file_note(kept, _message_size(messages))
    if not note:
        return messages
    return [*messages, {"role": "user", "content": note}]


async def _maybe_compact(db, conversation, messages: list[dict], workspace: Path) -> tuple[list[dict], bool]:
    messages = shrink_tool_results(messages)
    if _message_size(messages) < COMPACT_CHARS:
        return messages, False
    from ..services.runtime import compact_conversation

    summary = await compact_conversation(db, conversation, hub)
    if summary in {"对话还比较短，先不压缩。", "剩下的都是一组工具调用，先不压缩。"}:
        return messages, False
    return _with_restored_files(build_model_messages(db, conversation, workspace)), True


async def compact_transcript(
    messages: list[dict],
    provider,
    model: str,
    workspace: Path,
    conversation_id: int | None = None,
    previous: str = "",
    fallback_model: str = "",
    force: bool = False,
) -> tuple[list[dict], str, bool]:
    """子代理没有会话行，压缩直接改内存里的消息。主对话仍走数据库里的压缩。"""
    from .memory import clip_transcript, compact_source, summarize_history, transcript_text

    messages = shrink_tool_results(list(messages))
    size = _message_size(messages)
    if not force and size < COMPACT_CHARS:
        return messages, previous, False
    start = 0
    while start < len(messages) and messages[start].get("role") == "system":
        start += 1
    body = messages[start:]
    if len(body) < 5:
        return messages, previous, False
    keep = 4
    while keep < len(body) and body[-keep].get("role") == "tool":
        keep += 1
    if keep >= len(body):
        return messages, previous, False
    old = body[:-keep]
    transcript = clip_transcript(
        [
            (
                str(item.get("role") or ""),
                transcript_text(str(item.get("role") or ""), item.get("content"), item.get("tool_calls")),
            )
            for item in old
        ]
    )
    material = compact_source(workspace, conversation_id, previous, transcript)
    summary = await summarize_history(provider, model, material, fallback_model)
    packed = _with_restored_files(
        messages[:start] + [{"role": "user", "content": "更早对话的摘要：\n" + summary}] + body[-keep:]
    )
    return packed, summary, True


def build_model_messages(db, conversation, workspace: Path) -> list[dict]:
    from sqlalchemy import select

    from ..models import Task
    from .memory import import_legacy, memory_prompt, session_notes_for_prompt

    try:
        import_legacy(db, workspace)
    except Exception:
        logger.exception("导入旧记忆失败")
    tasks = db.scalars(
        select(Task).where(Task.conversation_id == conversation.id).order_by(Task.id.asc())
    ).all()
    skills = load_skills(db, workspace)
    brief = workspace_brief(workspace)
    info = {
        "workspace": str(workspace),
        "platform": __import__("platform").platform(),
        "permission_mode": conversation.permission_mode,
        "plan_file": f".harness/plans/{plan_filename(conversation.id)}",
        "provider": conversation.provider,
        "model": conversation.model,
        "memories": memory_prompt(workspace),
        "session_notes": session_notes_for_prompt(workspace, conversation.id),
        "skills": "\n".join(f"- {item['name']}: {item['description']}" for item in skills),
        "tasks": "\n".join(f"- #{item.id} [{item.status}] {item.title}" for item in tasks),
        "summary": conversation.summary or "",
        "instructions": brief["instructions"],
        "git": brief["git"],
        "agents": catalog(workspace),
        "mcp": prompt_text(),
    }
    messages: list[dict] = [{"role": "system", "content": system_prompt(info)}]
    role_note = (getattr(conversation, "member_prompt", None) or "").strip()
    if role_note:
        messages[0]["content"] += "\n\n你在群聊里的身份：\n" + role_note
    rows = db.scalars(
        select(Message)
        .where(
            Message.conversation_id == conversation.id,
            Message.in_context.is_(True),
            Message.role != "divider",
        )
        .order_by(Message.id.asc())
    ).all()
    for row in rows:
        if row.role == "user":
            messages.append({"role": "user", "content": _user_content(db, conversation, row)})
        elif row.role == "assistant":
            item: dict = {"role": "assistant", "content": row.content or None}
            if row.reasoning and _keeps_reasoning(conversation.model):
                item["reasoning_content"] = row.reasoning
            if row.tool_calls:
                item["tool_calls"] = []
                for call in row.tool_calls:
                    arguments = call.get("arguments") or {}
                    if not isinstance(arguments, str):
                        arguments = json.dumps(arguments, ensure_ascii=False)
                    item["tool_calls"].append(
                        {
                            "id": call.get("id"),
                            "type": "function",
                            "function": {"name": call.get("name"), "arguments": arguments},
                        }
                    )
            messages.append(item)
        elif row.role == "tool":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": row.tool_call_id,
                    "content": row.content or "（无输出）",
                }
            )
    return shrink_tool_results(_without_orphan_tools(messages))


def _without_orphan_tools(messages: list[dict]) -> list[dict]:
    """工具结果必须紧跟在带 tool_calls 的助手消息后面，否则接口会直接拒绝。"""
    cleaned: list[dict] = []
    pending: set[str] = set()
    for item in messages:
        if item.get("role") == "tool":
            call_id = str(item.get("tool_call_id") or "")
            if call_id and call_id in pending:
                cleaned.append(item)
                pending.discard(call_id)
            continue
        if pending and cleaned and cleaned[-1].get("role") == "assistant" and cleaned[-1].get("tool_calls"):
            cleaned.pop()
        pending = set()
        cleaned.append(item)
        if item.get("role") == "assistant":
            pending = {str(call.get("id")) for call in item.get("tool_calls") or [] if call.get("id")}
    if pending and cleaned and cleaned[-1].get("role") == "assistant" and cleaned[-1].get("tool_calls"):
        cleaned.pop()
    return cleaned


async def run_loop(messages, provider, ctx: ToolContext, max_steps: int = 6, allowed: list[str] | None = None) -> str:
    """子代理和主对话走同一套循环：长度续写、停止钩子、压缩都在。步数只计工具之后的下一问。"""
    if ctx.db is not None:
        try:
            await refresh(ctx.db, force=False)
        except Exception:
            logger.exception("刷新 MCP 工具失败")
    final = ""
    turns = 0
    recoveries = 0
    context_retries = 0
    carried = ""
    root = ctx.origin_workspace or ctx.workspace
    while True:
        if max_steps and turns >= max_steps:
            return final or "子代理达到步数上限"
        if ctx.conversation_key and _stopped(ctx.conversation_key, ctx.turn_token):
            return final or "已停止"
        ready = take_job_notices(ctx)
        if ready:
            _push_notice(messages, ready)
        notes = _take_mail(getattr(ctx, "agent_run_id", ""))
        if notes:
            messages.append({"role": "user", "content": "来自其他代理的消息：\n" + notes})
        fallback_model = ""
        if ctx.db is not None:
            try:
                from ..services.runtime import all_settings

                fallback_model = (all_settings(ctx.db).get("fallback_model") or "").strip()
            except Exception:
                fallback_model = ""
        messages, carried, _did = await compact_transcript(
            messages,
            provider,
            ctx.model,
            root,
            previous=carried,
            fallback_model=fallback_model,
        )

        async def on_start(item):
            if ctx.emit:
                await ctx.emit(
                    {
                        "type": "tool_start",
                        "id": item["id"],
                        "name": item["name"],
                        "arguments": item["arguments"],
                        "depth": ctx.depth,
                    }
                )

        try:
            driven = await _drive(
                provider,
                messages,
                schemas(allowed, workspace=root),
                ctx.model,
                ctx,
                lambda: bool(ctx.conversation_key) and _stopped(ctx.conversation_key, ctx.turn_token),
                on_start=on_start,
                query_source="agent",
                fallback_model=fallback_model,
            )
        except ModelFallback as exc:
            ctx.model = exc.fallback
            driven = await _drive(
                provider,
                messages,
                schemas(allowed, workspace=root),
                exc.fallback,
                ctx,
                lambda: bool(ctx.conversation_key) and _stopped(ctx.conversation_key, ctx.turn_token),
                on_start=on_start,
                query_source="agent",
            )
        except RuntimeError as exc:
            if context_retries >= CONTEXT_RECOVERIES or not _context_overflow(str(exc)):
                raise
            context_retries += 1
            messages, carried, did = await compact_transcript(
                messages,
                provider,
                ctx.model,
                root,
                previous=carried,
                fallback_model=fallback_model,
                force=True,
            )
            if not did:
                raise
            continue
        text = driven["text"]
        reasoning = driven["reasoning"]
        finish = driven["finish"]
        stored = driven["calls"]
        results = driven["results"]
        stopping = bool(ctx.conversation_key) and _stopped(ctx.conversation_key, ctx.turn_token)
        if not stored:
            if jobs_pending(ctx):
                note = await _wait_background(ctx)
                if note:
                    if text or reasoning:
                        messages.append(_assistant_payload(text, ctx.model, reasoning))
                    _push_notice(messages, note)
                    continue
            if stopping:
                return text or "已停止"
            if finish == "length" and recoveries < LENGTH_RECOVERIES and (text.strip() or reasoning.strip()):
                messages.append(_assistant_payload(text, ctx.model, reasoning))
                messages.append({"role": "user", "content": LENGTH_NUDGE})
                recoveries += 1
                continue
            from .hooks import stop_feedback

            feedback = await stop_feedback(ctx.workspace, text)
            if feedback:
                recoveries = 0
                note = "停止钩子要求先处理这些反馈，然后再结束：\n" + feedback
                messages.append(_assistant_payload(text, ctx.model, reasoning))
                messages.append({"role": "user", "content": note})
                continue
            if text or reasoning:
                messages.append(_assistant_payload(text, ctx.model, reasoning))
            return text
        messages.append(
            _assistant_payload(
                text,
                ctx.model,
                reasoning,
                [
                    {
                        "id": item["id"],
                        "type": "function",
                        "function": {
                            "name": item["name"],
                            "arguments": json.dumps(item["arguments"], ensure_ascii=False),
                        },
                    }
                    for item in stored
                ],
            )
        )
        for item, result in zip(stored, results):
            if ctx.emit:
                await ctx.emit(
                    {
                        "type": "tool_end",
                        "id": item["id"],
                        "name": item["name"],
                        "ok": result.ok,
                        "preview": (result.text or "")[:800],
                        "depth": ctx.depth,
                    }
                )
            messages.append({"role": "tool", "tool_call_id": item["id"], "content": _tool_content(result, ctx.model)})
            final = result.text
        if len(results) < len(stored):
            return final or "已停止"
        final = text or final
        turns += 1
        recoveries = 0
        context_retries = 0


async def run_turn(db, conversation, content: str, attachment_ids: list[int], emit, token: str) -> None:
    from datetime import datetime

    from ..services.runtime import all_settings, handle_slash

    content = (content or "").strip()
    if not content and not attachment_ids:
        await emit({"type": "error", "message": "消息是空的"})
        return

    stored = all_settings(db)
    workspace = Path(conversation.workspace or stored["workspace"])
    if not workspace.exists():
        await emit({"type": "error", "message": f"工作区不存在: {workspace}"})
        return

    user = Message(conversation_id=conversation.id, role="user", content=content, status="done")
    db.add(user)
    db.commit()
    linked = []
    for attachment_id in attachment_ids or []:
        attachment = db.get(Attachment, int(attachment_id))
        if attachment and attachment.conversation_id == conversation.id:
            attachment.message_id = user.id
            linked.append(f"{attachment.filename} ({attachment.path})")
    if linked:
        extra = "附件: " + ", ".join(linked)
        user.content = f"{user.content}\n{extra}".strip()
        db.commit()
    await emit({"type": "user", "message_id": user.id, "content": user.content})

    reply = await handle_slash(db, conversation, user.content, hub)
    if reply is not None:
        assistant = Message(
            conversation_id=conversation.id,
            role="assistant",
            content=reply,
            status="done",
        )
        db.add(assistant)
        _touch_conversation(conversation, user.content, reply)
        db.commit()
        await emit({"type": "start", "message_id": assistant.id})
        await emit({"type": "token", "text": reply, "message_id": assistant.id})
        await emit({"type": "done", "message_id": assistant.id, "content": reply, "usage": {}})
        return

    ctx = ToolContext(
        db=db,
        workspace=workspace,
        conversation_id=conversation.id,
        permission_mode=conversation.permission_mode or stored["permission_mode"],
        broker=broker,
        emit=emit,
        provider_name=conversation.provider,
        model=conversation.model,
        depth=0,
        hub=hub,
        turn_token=token,
        conversation_key=conversation.id,
        origin_workspace=workspace,
        history_batch=token,
    )
    provider = hub.resolve(conversation.provider, db)
    try:
        await refresh(db, force=True)
    except Exception:
        logger.exception("刷新 MCP 工具失败")
    messages = build_model_messages(db, conversation, workspace)
    total_prompt = 0
    total_completion = 0
    total_cache_hit = 0
    total_cache_miss = 0
    final_text = ""
    final_id = None
    recoveries = 0
    context_retries = 0
    media_retries = 0
    compacted = False
    mutated_after_compact = False
    active_model = conversation.model
    fallback_model = (stored.get("fallback_model") or "").strip()

    try:
        while True:
            if _stopped(conversation.id, token):
                final_text = final_text or "已停止"
                break
            ready = take_job_notices(ctx)
            if ready:
                _push_notice(messages, ready)
            messages, did_compact = await _maybe_compact(db, conversation, messages, workspace)
            if did_compact:
                compacted = True
                mutated_after_compact = False
            assistant = Message(
                conversation_id=conversation.id,
                role="assistant",
                content="",
                status="streaming",
            )
            db.add(assistant)
            db.commit()
            final_id = assistant.id
            await emit({"type": "start", "message_id": assistant.id})
            async def on_text(piece, message_id=assistant.id):
                await emit({"type": "token", "text": piece, "message_id": message_id})

            async def on_reasoning(piece, message_id=assistant.id):
                await emit({"type": "reasoning", "text": piece, "message_id": message_id})

            async def on_start(item, message_id=assistant.id):
                await emit(
                    {
                        "type": "tool_start",
                        "message_id": message_id,
                        "id": item["id"],
                        "name": item["name"],
                        "arguments": item["arguments"],
                    }
                )

            try:
                driven = await _drive(
                    provider,
                    messages,
                    schemas(workspace=workspace),
                    active_model,
                    ctx,
                    lambda: _stopped(conversation.id, token),
                    on_text=on_text,
                    on_reasoning=on_reasoning,
                    on_start=on_start,
                    query_source="foreground",
                    fallback_model=fallback_model,
                )
            except ModelFallback as exc:
                note = f"原模型 {exc.original} 连续过载，这一轮改用 {exc.fallback}。"
                assistant.content = note
                assistant.status = "done"
                db.commit()
                await emit({"type": "token", "text": note, "message_id": assistant.id})
                active_model = exc.fallback
                ctx.model = exc.fallback
                fallback_model = ""
                continue
            except RuntimeError as exc:
                if media_retries < MEDIA_RECOVERIES and _media_rejected(str(exc)) and _strip_media(messages):
                    media_retries += 1
                    note = "图片或文档过大，已从请求里去掉后再试一次。"
                    assistant.content = note
                    assistant.status = "done"
                    db.commit()
                    await emit({"type": "token", "text": note, "message_id": assistant.id})
                    continue
                if context_retries >= CONTEXT_RECOVERIES or not _context_overflow(str(exc)):
                    raise
                context_retries += 1
                assistant.in_context = False
                assistant.status = "done"
                db.commit()
                from ..services.runtime import compact_conversation

                summary = await compact_conversation(db, conversation, hub)
                if summary not in {"对话还比较短，先不压缩。", "剩下的都是一组工具调用，先不压缩。"}:
                    compacted = True
                    mutated_after_compact = False
                    messages = _with_restored_files(build_model_messages(db, conversation, workspace))
                continue
            acc = driven["text"]
            reasoning = driven["reasoning"]
            finish = driven["finish"]
            stored_calls = driven["calls"]
            results = driven["results"]
            total_prompt += driven["prompt"]
            total_completion += driven["completion"]
            total_cache_hit += driven["cache_hit"]
            total_cache_miss += driven["cache_miss"]
            assistant.content = acc
            assistant.reasoning = reasoning
            assistant.prompt_tokens = total_prompt
            assistant.completion_tokens = total_completion
            stopping = _stopped(conversation.id, token)
            if stopping and not stored_calls:
                assistant.status = "done"
                final_text = acc or "已停止"
                db.commit()
                break
            if not stored_calls:
                assistant.status = "done"
                final_text = acc
                db.commit()
                if jobs_pending(ctx) and not _stopped(conversation.id, token):
                    note = await _wait_background(ctx)
                    if note:
                        messages.append(_assistant_payload(acc, active_model, reasoning))
                        _push_notice(messages, note)
                        await _remember_session(conversation, messages, workspace, provider)
                        continue
                if finish == "length" and recoveries < LENGTH_RECOVERIES and (acc.strip() or reasoning.strip()):
                    messages.append(_assistant_payload(acc, active_model, reasoning))
                    messages.append({"role": "user", "content": LENGTH_NUDGE})
                    recoveries += 1
                    await _remember_session(conversation, messages, workspace, provider)
                    continue
                if not stopping:
                    from .hooks import stop_feedback

                    feedback = await stop_feedback(workspace, acc)
                    if feedback:
                        recoveries = 0
                        note = "停止钩子要求先处理这些反馈，然后再结束：\n" + feedback
                        hook_row = Message(
                            conversation_id=conversation.id,
                            role="user",
                            content=note,
                            status="done",
                        )
                        db.add(hook_row)
                        db.commit()
                        await emit({"type": "user", "message_id": hook_row.id, "content": note})
                        messages.append(_assistant_payload(acc, active_model, reasoning))
                        messages.append({"role": "user", "content": note})
                        await _remember_session(conversation, messages, workspace, provider)
                        continue
                messages.append(_assistant_payload(acc, active_model, reasoning))
                await _remember_session(conversation, messages, workspace, provider)
                break
            assistant.tool_calls = stored_calls
            assistant.status = "done"
            db.commit()
            messages.append(
                _assistant_payload(
                    acc,
                    active_model,
                    reasoning,
                    [
                        {
                            "id": item["id"],
                            "type": "function",
                            "function": {
                                "name": item["name"],
                                "arguments": json.dumps(item["arguments"], ensure_ascii=False),
                            },
                        }
                        for item in stored_calls
                    ],
                )
            )
            halted = len(results) < len(stored_calls)
            for item, result in zip(stored_calls, results):
                tool_row = Message(
                    conversation_id=conversation.id,
                    role="tool",
                    content=result.text or "（无输出）",
                    tool_call_id=item["id"],
                    tool_name=item["name"],
                    status="done" if result.ok else "error",
                )
                db.add(tool_row)
                db.commit()
                attachments = []
                for attachment_id in result.attachments:
                    attachment = db.get(Attachment, attachment_id)
                    if attachment:
                        attachment.message_id = assistant.id
                        attachments.append(
                            {
                                "id": attachment.id,
                                "filename": attachment.filename,
                                "size": attachment.size,
                            }
                        )
                db.commit()
                shown, embedded = split_embedded_diff(result.text or "")
                await emit(
                    {
                        "type": "tool_end",
                        "message_id": assistant.id,
                        "id": item["id"],
                        "name": item["name"],
                        "ok": result.ok,
                        "preview": shown[:800],
                        "diff": result.diff or embedded,
                        "attachments": attachments,
                    }
                )
                messages.append({"role": "tool", "tool_call_id": item["id"], "content": _tool_content(result, active_model)})
                if result.ok and item["name"] in FILE_MUTATIONS and compacted:
                    mutated_after_compact = True
            await _remember_session(conversation, messages, workspace, provider)
            if stopping or halted:
                final_text = acc or "已停止"
                break
            recoveries = 0
            context_retries = 0
    except Exception as exc:
        logger.exception("model turn failed")
        await emit({"type": "error", "message": str(exc)})
        if final_id:
            from sqlalchemy import select

            last = db.get(Message, final_id)
            if last and last.status == "streaming":
                last.status = "error"
                last.content = last.content or str(exc)
            if last and last.tool_calls:
                have = {
                    row.tool_call_id
                    for row in db.scalars(
                        select(Message).where(Message.conversation_id == conversation.id, Message.role == "tool")
                    )
                    if row.tool_call_id
                }
                for item in last.tool_calls:
                    call_id = item.get("id")
                    if call_id and call_id not in have:
                        db.add(
                            Message(
                                conversation_id=conversation.id,
                                role="tool",
                                content=str(exc),
                                tool_call_id=call_id,
                                tool_name=item.get("name") or "",
                                status="error",
                            )
                        )
            db.commit()
        return

    if compacted and mutated_after_compact:
        try:
            from ..services.runtime import compact_conversation

            await compact_conversation(db, conversation, hub)
        except Exception:
            logger.exception("结束前更新摘要失败")
    if not total_prompt:
        total_prompt = max(1, sum(len(str(item.get("content") or "")) for item in messages) // 2)
    if not total_completion:
        total_completion = max(1, len(final_text) // 2) if final_text else 0
    if total_prompt or total_completion:
        db.add(
            UsageEvent(
                conversation_id=conversation.id,
                user_id=conversation.user_id,
                provider=conversation.provider,
                model=active_model,
                prompt_tokens=total_prompt,
                completion_tokens=total_completion,
            )
        )
    _touch_conversation(conversation, content, final_text)
    db.commit()
    await emit(
        {
            "type": "done",
            "message_id": final_id,
            "content": final_text,
            "usage": {
                "prompt_tokens": total_prompt,
                "completion_tokens": total_completion,
                "cache_hit_tokens": total_cache_hit,
                "cache_miss_tokens": total_cache_miss,
                "cost_usd": estimate_cost(conversation.model, total_prompt, total_completion),
            },
        }
    )


def _touch_conversation(conversation, user_text: str, assistant_text: str) -> None:
    from datetime import datetime

    preview_source = (user_text or assistant_text or "").replace("\n", " ").strip()
    conversation.preview = preview_source[:120]
    if conversation.title in {"", "新会话"} and user_text and not user_text.startswith("/"):
        conversation.title = user_text.replace("\n", " ").strip()[:24] or "新会话"
    conversation.updated_at = datetime.now()


async def iter_events(db, conversation, content: str, attachment_ids: list[int]):
    import asyncio

    token = uuid.uuid4().hex
    turn_tokens[conversation.id] = token
    queue: asyncio.Queue = asyncio.Queue()

    async def emit(event):
        await queue.put(event)

    async def work():
        try:
            await run_turn(db, conversation, content, attachment_ids, emit, token)
        except Exception as exc:
            logger.exception("turn crashed")
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
