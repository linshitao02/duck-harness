import logging
import os
import platform
import shutil
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from sqlalchemy import func, or_, select, text

from ..access import current_user_id, owner_kwargs, private_workspace, reset_actor, set_actor
from ..vault import reveal, seal, sealed
from ..config import ROOT_DIR, UPLOAD_DIR, get_settings
from ..agent.history import discard_journal, sweep_history
from ..agent.diffutil import split_embedded_diff
from ..models import (
    Attachment,
    Conversation,
    McpServer,
    Message,
    Schedule,
    Setting,
    Skill,
    Task,
    UsageEvent,
    User,
    UserSetting,
)
from .cost import estimate_cost
from .seed import seed_if_empty

logger = logging.getLogger("harness.runtime")

PERMISSION_MODES = {"auto", "ask", "plan", "edits", "dontAsk"}

SETTING_KEYS = {
    "workspace",
    "provider",
    "model",
    "permission_mode",
    "user_name",
    "max_steps",
    "ollama_base",
    "ollama_model",
    "deepseek_model",
    "deepseek_base",
    "fallback_model",
    "theme",
    "accent",
    "spellcheck",
    "send_shortcut",
    "font_family",
    "font_size",
}
FONT_FAMILIES = {"grok", "dengxian", "yahei", "segoe", "sourcehan", "kaiti", "song"}
FONT_SIZES = {"11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "22"}
SECRET_KEYS = {"deepseek_api_key"}


def default_map() -> dict[str, str]:
    settings = get_settings()
    has_key = bool(settings.deepseek_api_key)
    return {
        "workspace": os.environ.get("HARNESS_WORKSPACE", str(ROOT_DIR)),
        "provider": "deepseek" if has_key else "ollama",
        "model": "deepseek-chat" if has_key else "qwen3:4b",
        "permission_mode": "auto",
        "user_name": "用户",
        "max_steps": "200",
        "ollama_base": settings.ollama_base_url,
        "ollama_model": "qwen3:4b",
        "deepseek_model": "deepseek-chat",
        "deepseek_base": settings.deepseek_base_url,
        "fallback_model": "",
        "theme": "system",
        "accent": "#ff4b93",
        "spellcheck": "1",
        "send_shortcut": "enter",
        "font_family": "grok",
        "font_size": "17",
    }


def _plain_settings(rows) -> tuple[dict[str, str], bool]:
    current: dict[str, str] = {}
    changed = False
    for row in rows:
        if row.name in SECRET_KEYS:
            plain = reveal(row.value)
            if row.value and not sealed(row.value):
                row.value = seal(plain)
                changed = True
            current[row.name] = plain
        else:
            current[row.name] = row.value
    return current, changed


def _stored_value(key: str, value: str) -> str:
    return seal(value) if key in SECRET_KEYS else value


def ensure_defaults(db) -> dict[str, str]:
    user = db.get(User, 1)
    if user is None:
        user = User(id=1, name="用户")
        db.add(user)
    rows = db.scalars(select(Setting)).all()
    current, changed = _plain_settings(rows)
    for key, value in default_map().items():
        if key not in current:
            db.add(Setting(name=key, value=_stored_value(key, value)))
            current[key] = value
            changed = True
    if user and current.get("user_name") and user.name != current["user_name"]:
        user.name = current["user_name"]
        changed = True
    if changed:
        db.commit()
    repair_workspaces(db, current)
    return current


def _is_dir(path: str | None) -> bool:
    if not path:
        return False
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def workspace_fallback() -> str:
    preferred = os.environ.get("HARNESS_WORKSPACE") or str(ROOT_DIR)
    if _is_dir(preferred):
        return str(Path(preferred))
    if _is_dir(str(ROOT_DIR)):
        return str(ROOT_DIR)
    return preferred


def repair_workspaces(db, current: dict[str, str] | None = None) -> str:
    """目录改名后，把已经不存在的工作区改到当前项目目录。"""
    if current is None:
        current = {row.name: row.value for row in db.scalars(select(Setting)).all()}
    fallback = workspace_fallback()
    if not _is_dir(fallback):
        return current.get("workspace") or fallback
    changed = False
    if not _is_dir(current.get("workspace")):
        _write_setting(db, current, "workspace", fallback)
        changed = True
    saved = current.get("workspace") or fallback
    for row in db.scalars(select(Conversation)).all():
        if row.workspace and not _is_dir(row.workspace):
            row.workspace = saved
            changed = True
    if changed:
        db.commit()
    return saved


def all_settings(db) -> dict[str, str]:
    uid = current_user_id()
    if uid:
        user = db.get(User, uid)
        if user is not None:
            return ensure_user_settings(db, user)
    return ensure_defaults(db)


def ensure_user_settings(db, user: User) -> dict[str, str]:
    rows = db.scalars(select(UserSetting).where(UserSetting.user_id == user.id)).all()
    current, changed = _plain_settings(rows)
    defaults = default_map()
    if current:
        for key, value in defaults.items():
            if key not in current:
                if key == "workspace":
                    value = private_workspace(user.id)
                db.add(UserSetting(user_id=user.id, name=key, value=_stored_value(key, value)))
                current[key] = value
                changed = True
        if changed:
            db.commit()
        return current
    others = db.scalar(select(func.count()).select_from(UserSetting)) or 0
    if others == 0:
        for row in db.scalars(select(Setting)).all():
            if row.name == "workspace" or not row.value:
                continue
            if row.name in defaults or row.name in SECRET_KEYS:
                defaults[row.name] = reveal(row.value) if row.name in SECRET_KEYS else row.value
    else:
        defaults["user_name"] = user.name or user.username or "用户"
    defaults["workspace"] = private_workspace(user.id)
    for key, value in defaults.items():
        db.add(UserSetting(user_id=user.id, name=key, value=_stored_value(key, value)))
    db.commit()
    return defaults


def _write_setting(db, current: dict, key: str, value: str) -> None:
    stored = _stored_value(key, value)
    uid = current_user_id()
    if uid:
        row = db.get(UserSetting, (uid, key))
        if row is None:
            db.add(UserSetting(user_id=uid, name=key, value=stored))
        else:
            row.value = stored
    else:
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(name=key, value=stored))
        else:
            row.value = stored
    current[key] = value


def _clean_setting(key: str, value) -> str | None:
    text = str(value if value is not None else "").strip()
    if key == "theme" and text not in {"system", "light", "dark"}:
        return None
    if key == "send_shortcut" and text not in {"enter", "ctrl_enter"}:
        return None
    if key == "spellcheck":
        return "0" if text in {"0", "false", "False"} else "1"
    if key == "font_family" and text not in FONT_FAMILIES:
        return None
    if key == "font_size" and text not in FONT_SIZES:
        return None
    if key == "accent" and not (len(text) == 7 and text.startswith("#") and all(ch in "0123456789abcdefABCDEF" for ch in text[1:])):
        return None
    return text


def canonical_mode(mode: str | None) -> str:
    """把 clawd 的模式名收成这里使用的名字。空值按自动。"""
    text = (mode or "").strip()
    mapped = {
        "acceptedits": "edits",
        "bypasspermissions": "auto",
        "default": "ask",
        "dontask": "dontAsk",
    }.get(text.lower())
    if mapped:
        return mapped
    return text or "auto"


def set_settings(db, values: dict) -> dict[str, str]:
    current = ensure_defaults(db)
    clear_key = values.get("clear_deepseek_key")
    if clear_key is True or str(clear_key).lower() in {"1", "true"}:
        _write_setting(db, current, "deepseek_api_key", "")
    for key, value in values.items():
        if key == "clear_deepseek_key":
            continue
        if key in SECRET_KEYS:
            secret = str(value or "").strip()
            if secret:
                _write_setting(db, current, key, secret)
            continue
        if key not in SETTING_KEYS:
            continue
        if key == "permission_mode":
            value = canonical_mode(str(value or ""))
            if value not in PERMISSION_MODES:
                continue
        cleaned = _clean_setting(key, value)
        if cleaned is None:
            continue
        _write_setting(db, current, key, cleaned)
    if "user_name" in values:
        uid = current_user_id()
        user = db.get(User, uid) if uid else db.get(User, 1)
        if user:
            user.name = str(values["user_name"])[:64]
    db.commit()
    return {key: value for key, value in current.items() if key not in SECRET_KEYS}


def deepseek_credentials(db) -> tuple[str, str, str]:
    stored = all_settings(db)
    saved = (stored.get("deepseek_api_key") or "").strip()
    env_key = (get_settings().deepseek_api_key or "").strip()
    key = saved or env_key
    source = "settings" if saved else ("env" if env_key else "")
    base = (stored.get("deepseek_base") or "").strip() or get_settings().deepseek_base_url
    return base.rstrip("/"), key, source


def serialize_conversation(row: Conversation) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "preview": row.preview or "",
        "icon_key": row.icon_key,
        "icon_color": row.icon_color,
        "pinned": bool(row.pinned),
        "unread": bool(row.unread),
        "hidden": bool(row.hidden),
        "group_name": row.group_name or "",
        "provider": row.provider,
        "model": row.model,
        "permission_mode": row.permission_mode,
        "workspace": row.workspace or "",
        "summary": row.summary or "",
        "kind": row.kind or "chat",
        "room_id": row.room_id,
        "member_name": row.member_name or "",
        "updated_at": row.updated_at.isoformat(sep=" ", timespec="seconds") if row.updated_at else "",
    }


def serialize_attachment(row: Attachment) -> dict:
    return {
        "id": row.id,
        "filename": row.filename,
        "mime": row.mime,
        "size": row.size,
        "message_id": row.message_id,
    }


def present_messages(db, conversation_id: int) -> list[dict]:
    rows = db.scalars(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id.asc())
    ).all()
    attachments = db.scalars(
        select(Attachment).where(Attachment.conversation_id == conversation_id).order_by(Attachment.id.asc())
    ).all()
    by_message: dict[int, list] = {}
    for item in attachments:
        if item.message_id:
            by_message.setdefault(item.message_id, []).append(serialize_attachment(item))
    tool_rows = {row.tool_call_id: row for row in rows if row.role == "tool" and row.tool_call_id}
    presented = []
    for row in rows:
        if row.role == "tool" or (row.channel or "public") in {"private", "intake"}:
            continue
        item = {
            "id": row.id,
            "role": row.role,
            "content": row.content or "",
            "speaker": row.speaker or "",
            "handoff_target": row.handoff_target or "",
            "card": row.card if isinstance(row.card, dict) else None,
            "reasoning": row.reasoning or "",
            "status": row.status,
            "created_at": row.created_at.isoformat(sep=" ", timespec="seconds") if row.created_at else "",
            "attachments": by_message.get(row.id, []),
            "tools": [],
        }
        for call in row.tool_calls or []:
            follow = tool_rows.get(call.get("id"))
            raw = follow.content if follow else ""
            shown, diff = split_embedded_diff(raw)
            item["tools"].append(
                {
                    "id": call.get("id"),
                    "name": call.get("name"),
                    "arguments": call.get("arguments") or {},
                    "result": shown,
                    "diff": diff,
                    "ok": follow.status != "error" if follow else True,
                }
            )
        presented.append(item)
    return presented


def list_conversations(db, query: str = "") -> list[dict]:
    statement = select(Conversation).order_by(
        Conversation.pinned.desc(), Conversation.updated_at.desc(), Conversation.id.desc()
    )
    uid = current_user_id()
    if uid:
        statement = statement.where(Conversation.user_id == uid)
    if query:
        like = f"%{query}%"
        statement = statement.where(or_(Conversation.title.like(like), Conversation.preview.like(like)))
    return [serialize_conversation(row) for row in db.scalars(statement.limit(200)).all()]


def usage_summary(db) -> dict:
    total = select(
        func.coalesce(func.sum(UsageEvent.prompt_tokens), 0),
        func.coalesce(func.sum(UsageEvent.completion_tokens), 0),
    )
    grouped = select(
        UsageEvent.model, func.sum(UsageEvent.prompt_tokens), func.sum(UsageEvent.completion_tokens)
    ).group_by(UsageEvent.model)
    uid = current_user_id()
    if uid:
        total = total.where(UsageEvent.user_id == uid)
        grouped = grouped.where(UsageEvent.user_id == uid)
    prompt, completion = db.execute(total).one()
    rows = db.execute(grouped).all()
    by_model = []
    total_cost = 0.0
    for model, pin, pout in rows:
        cost = estimate_cost(model or "", int(pin or 0), int(pout or 0))
        total_cost += cost
        by_model.append(
            {
                "model": model,
                "prompt_tokens": int(pin or 0),
                "completion_tokens": int(pout or 0),
                "cost_usd": cost,
            }
        )
    return {
        "prompt_tokens": int(prompt or 0),
        "completion_tokens": int(completion or 0),
        "cost_usd": round(total_cost, 8),
        "by_model": by_model,
        "note": "费用是按公开价格估算的，本地 Ollama 计为 0。",
    }


async def doctor(db) -> dict:
    from ..db import database_info

    checks = []
    info = database_info()
    try:
        db.execute(text("SELECT 1"))
        checks.append({"name": info["database"], "ok": True, "detail": info["detail"]})
    except Exception as exc:
        checks.append({"name": info["database"], "ok": False, "detail": str(exc)})
    _base, has_key_value, _source = deepseek_credentials(db)
    checks.append({"name": "deepseek_key", "ok": bool(has_key_value), "detail": "已配置" if has_key_value else "未配置"})
    base = all_settings(db).get("ollama_base") or get_settings().ollama_base_url
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(base.rstrip("/") + "/api/tags")
            response.raise_for_status()
            names = [item.get("name", "") for item in response.json().get("models", [])]
        checks.append({"name": "ollama", "ok": True, "detail": "、".join(names) or "服务在线，但没有模型"})
    except Exception as exc:
        checks.append({"name": "ollama", "ok": False, "detail": str(exc)})
    workspace = Path(all_settings(db)["workspace"])
    checks.append(
        {
            "name": "workspace",
            "ok": workspace.exists() and os.access(workspace, os.W_OK),
            "detail": str(workspace),
        }
    )
    dist = ROOT_DIR / "frontend" / "dist" / "index.html"
    checks.append({"name": "frontend", "ok": dist.exists(), "detail": "已构建" if dist.exists() else "尚未构建"})
    checks.append({"name": "python", "ok": True, "detail": platform.python_version()})
    required = {info["database"], "workspace"}
    ready = all(item["ok"] for item in checks if item["name"] in required) and any(
        item["ok"] for item in checks if item["name"] in {"deepseek_key", "ollama"}
    )
    return {"ok": ready, "checks": checks}


async def compact_conversation(db, conversation, provider_hub) -> str:
    rows = db.scalars(
        select(Message)
        .where(
            Message.conversation_id == conversation.id,
            Message.in_context.is_(True),
            Message.role != "divider",
        )
        .order_by(Message.id.asc())
    ).all()
    if len(rows) < 5:
        return "对话还比较短，先不压缩。"
    keep = 4
    while keep < len(rows) and rows[-keep].role == "tool":
        keep += 1
    if keep >= len(rows):
        return "剩下的都是一组工具调用，先不压缩。"
    old = rows[:-keep]
    from ..agent.memory import clip_transcript, compact_source, summarize_history, transcript_text

    transcript = clip_transcript([(row.role, transcript_text(row.role, row.content, row.tool_calls)) for row in old])
    workspace = Path(conversation.workspace) if conversation.workspace else Path(all_settings(db)["workspace"])
    material = compact_source(workspace, conversation.id, conversation.summary or "", transcript)
    provider = provider_hub.resolve(conversation.provider, db)
    fallback_model = (all_settings(db).get("fallback_model") or "").strip()
    summary = await summarize_history(provider, conversation.model, material, fallback_model)
    conversation.summary = summary
    for row in old:
        row.in_context = False
    db.commit()
    return summary


async def handle_slash(db, conversation, content: str, provider_hub) -> str | None:
    text = (content or "").strip()
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    command = head.lower()
    rest = rest.strip()
    if command == "/help":
        return (
            "可用命令：/help、/doctor、/cost、/memory 内容、/tasks、/agents、/jobs、/permissions、/rewind、"
            "/mode auto|ask|plan|edits、/model deepseek deepseek-chat、/model ollama qwen3:4b、"
            "/compact、/skill 名称"
        )
    if command == "/doctor":
        report = await doctor(db)
        lines = ["环境检查 " + ("通过" if report["ok"] else "有未就绪项")]
        for item in report["checks"]:
            mark = "通过" if item["ok"] else "未通过"
            lines.append(f"- {item['name']}：{mark}，{item['detail']}")
        return "\n".join(lines)
    if command == "/cost":
        summary = usage_summary(db)
        return (
            f"累计输入 {summary['prompt_tokens']} token，输出 {summary['completion_tokens']} token，"
            f"估算费用 ${summary['cost_usd']}。{summary['note']}"
        )
    if command == "/memory":
        from ..agent.memory import save_memory, sync_index

        workspace = Path(conversation.workspace or all_settings(db)["workspace"])
        if not rest:
            listed = sync_index(workspace).strip()
            return listed or "还没有记忆。用法：/memory 要记住的内容"
        save_memory(workspace, rest[:40], rest, "user", rest[:80])
        return "已记住。"
    if command == "/tasks":
        rows = db.scalars(select(Task).where(Task.conversation_id == conversation.id).order_by(Task.id.asc())).all()
        if not rows:
            return "这个会话还没有任务。"
        return "\n".join(f"#{row.id} [{row.status}] {row.title}" for row in rows)
    if command == "/agents":
        from ..agent.agents import catalog

        workspace = Path(conversation.workspace) if conversation.workspace else None
        return catalog(workspace)
    if command == "/permissions":
        from ..agent.permissions import add_local_rule, clear_local_rules, describe_rules

        workspace = Path(conversation.workspace or all_settings(db)["workspace"])
        piece, _, rule = rest.partition(" ")
        action = piece.strip().lower()
        if not action:
            return describe_rules(workspace)
        if action == "clear":
            return clear_local_rules(workspace)
        if action in {"deny", "ask", "allow"}:
            return add_local_rule(workspace, action, rule.strip())
        return "用法：/permissions、/permissions deny bash(Remove-Item*)、/permissions clear"
    if command == "/rewind":
        from ..agent.history import rewind

        count = int(rest) if rest.isdigit() else 1
        workspace = Path(conversation.workspace or all_settings(db)["workspace"])
        restored = rewind(workspace, conversation.id, count)
        if not restored:
            return "没有可撤销的文件改动。"
        return "已撤销这些文件改动：\n" + "\n".join(restored)
    if command == "/jobs":
        from ..agent.state import JOBS

        if not JOBS:
            return "没有后台命令。"
        lines = []
        for job_id, job in JOBS.items():
            state = "已结束" if job.get("done") else "运行中"
            lines.append(f"#{job_id} [{state}] {job.get('command')}")
        return "\n".join(lines)
    if command == "/mode":
        mode = canonical_mode(rest or "auto")
        if mode not in PERMISSION_MODES:
            return "模式只能是 auto、ask、plan、edits 或 dontAsk。"
        conversation.permission_mode = mode
        db.commit()
        return f"当前会话权限已改为 {mode}。"
    if command == "/model":
        pieces = rest.split()
        if len(pieces) < 2 or pieces[0] not in {"deepseek", "ollama"}:
            return "用法：/model deepseek deepseek-chat 或 /model ollama qwen3:4b"
        conversation.provider = pieces[0]
        conversation.model = pieces[1]
        db.commit()
        return f"已切换到 {conversation.provider} / {conversation.model}。"
    if command == "/compact":
        summary = await compact_conversation(db, conversation, provider_hub)
        return "上下文已压缩。\n" + summary
    if command == "/skill":
        from ..agent.tools import load_skills

        workspace = Path(conversation.workspace) if conversation.workspace else None
        skills = {item["name"]: item for item in load_skills(db, workspace)}
        if not rest:
            return "可用技能：" + "、".join(skills) if skills else "没有技能"
        skill = skills.get(rest)
        if skill is None:
            return "没有这个技能。可用：" + "、".join(skills)
        return f"已载入技能 {rest}。\n{skill['body']}"
    return "不认识这个命令。输入 /help 查看。"


async def scheduler_loop() -> None:
    import asyncio

    while True:
        await asyncio.sleep(15)
        try:
            await run_due_schedules()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("scheduler tick failed")


async def run_due_schedules() -> int:
    from ..agent.loop import run_turn
    from ..db import open_session

    db = open_session()
    started = 0
    try:
        now = datetime.now()
        rows = db.scalars(
            select(Schedule).where(
                Schedule.enabled.is_(True),
                Schedule.next_run.is_not(None),
                Schedule.next_run <= now,
            )
        ).all()
        jobs = []
        for row in rows:
            row.last_run = now
            if row.every_seconds and row.every_seconds > 0:
                row.next_run = now + timedelta(seconds=max(int(row.every_seconds), 30))
            else:
                row.enabled = False
            jobs.append((row.conversation_id, row.prompt))
        db.commit()
        for conversation_id, prompt in jobs:
            if not conversation_id:
                continue
            conversation = db.get(Conversation, conversation_id)
            if conversation is None:
                continue

            async def ignore(_event):
                return None

            from ..access import protected_for
            from ..agent.loop import turn_tokens

            token = f"schedule-{conversation_id}-{int(now.timestamp())}-{started}"
            turn_tokens[conversation.id] = token
            actor = set_actor(
                conversation.user_id,
                protected_for(db, conversation.user_id) if conversation.user_id else (),
            )
            try:
                await run_turn(db, conversation, prompt, [], ignore, token)
            finally:
                reset_actor(actor)
            started += 1
        return started
    finally:
        db.close()


def raise_room_turn_limits(db) -> None:
    from ..agent.room import normalize_max_turns

    rows = db.scalars(select(Conversation).where(Conversation.kind == "group")).all()
    for row in rows:
        state = dict(row.room_state or {})
        limit = normalize_max_turns(state.get("max_turns"))
        if state.get("max_turns") == limit:
            continue
        state["max_turns"] = limit
        row.room_state = state


def startup() -> None:
    from ..db import init_db, session_scope

    init_db()
    with session_scope() as db:
        ensure_defaults(db)
        raise_room_turn_limits(db)
        sweep_orphaned_conversation_files(db)
        if get_settings().auto_seed:
            seed_if_empty(db)


def public_settings(db) -> dict:
    stored = all_settings(db)
    uid = current_user_id()
    user = db.get(User, uid) if uid else db.get(User, 1)
    _base, key, source = deepseek_credentials(db)
    visible = {name: value for name, value in stored.items() if name not in SECRET_KEYS}
    return {
        "values": visible,
        "user": {
            "id": user.id if user else None,
            "name": user.name if user else stored.get("user_name", "用户"),
            "username": (user.username or "") if user else "",
        },
        "deepseek_configured": bool(key),
        "deepseek_key_source": source,
        "providers": ["deepseek", "ollama"],
    }


def room_members(db, group: Conversation) -> list[Conversation]:
    """按拉进群时的顺序返回成员。没记顺序时按创建顺序。"""
    rows = db.scalars(select(Conversation).where(Conversation.room_id == group.id)).all()
    by_id = {row.id: row for row in rows}
    ordered = []
    for item in (group.room_state or {}).get("member_ids") or []:
        try:
            key = int(item)
        except (TypeError, ValueError):
            continue
        row = by_id.pop(key, None)
        if row is not None:
            ordered.append(row)
    ordered.extend(sorted(by_id.values(), key=lambda row: row.id))
    return ordered


def _linked_prompt(name: str) -> str:
    return f"你是{name}。在群里按这个身份完成交给你的任务。"


def _inside_dir(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _upload_dirs(conversation_id: int) -> list[Path]:
    root = UPLOAD_DIR.resolve()
    found = []
    if not root.is_dir():
        return found
    legacy = root / str(int(conversation_id))
    if legacy.is_dir() and not legacy.is_symlink() and _inside_dir(legacy, root):
        found.append(legacy)
    for child in root.iterdir():
        if not child.is_dir() or child.is_symlink() or not (child.name.startswith("u") and child.name[1:].isdigit()):
            continue
        nested = child / str(int(conversation_id))
        if nested.is_dir() and not nested.is_symlink() and _inside_dir(nested, root):
            found.append(nested)
    return found


def discard_uploads(conversation_id: int) -> bool:
    """删掉这条会话上传和生成的附件目录。"""
    removed = False
    for folder in _upload_dirs(conversation_id):
        shutil.rmtree(folder)
        removed = True
    return removed


def _known_workspaces(db) -> list[Path]:
    found = []
    seen = set()
    setting = db.get(Setting, "workspace")
    raw_values = [setting.value if setting else ""]
    raw_values.extend(db.scalars(select(Conversation.workspace)))
    for raw in raw_values:
        if not raw or not _is_dir(raw):
            continue
        try:
            resolved = str(Path(raw).resolve())
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        found.append(Path(resolved))
    return found


def _sweep_loose_uploads(db, live: set[int]) -> int:
    """删掉还在的会话目录里、已经没有附件记录的文件。"""
    referenced = set()
    for row in db.scalars(select(Attachment)):
        if not row.path:
            continue
        try:
            referenced.add(str(Path(row.path).resolve()))
        except OSError:
            continue
    removed = 0
    for conversation_id in live:
        for folder in _upload_dirs(conversation_id):
            _removed_here = _sweep_folder(folder, referenced)
            removed += _removed_here
    return removed


def _sweep_folder(folder: Path, referenced: set[str]) -> int:
    removed = 0
    for child in list(folder.iterdir()):
        if not child.is_file() or child.is_symlink() or not _inside_dir(child, folder):
            continue
        try:
            if str(child.resolve()) in referenced:
                continue
            child.unlink()
            removed += 1
        except OSError:
            logger.warning("清理落单附件失败：%s", child.name, exc_info=True)
    try:
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass
    return removed


def sweep_orphaned_conversation_files(db) -> None:
    """清掉已经没有会话的上传目录和撤销副本。"""
    live = set(db.scalars(select(Conversation.id)))
    removed_uploads = 0
    seen: set[int] = set()
    if UPLOAD_DIR.is_dir() and not UPLOAD_DIR.is_symlink():
        for child in list(UPLOAD_DIR.iterdir()):
            if not child.is_dir() or child.is_symlink():
                continue
            if child.name.isdigit():
                seen.add(int(child.name))
            elif child.name.startswith("u") and child.name[1:].isdigit():
                for nested in child.iterdir():
                    if nested.is_dir() and nested.name.isdigit():
                        seen.add(int(nested.name))
        for conversation_id in seen - live:
            try:
                if discard_uploads(conversation_id):
                    removed_uploads += 1
            except OSError:
                logger.warning("清理上传目录失败：%s", conversation_id, exc_info=True)
    removed_loose = 0
    try:
        removed_loose = _sweep_loose_uploads(db, live)
    except OSError:
        logger.warning("清理落单附件失败", exc_info=True)
    removed_journals = 0
    for workspace in _known_workspaces(db):
        try:
            removed_journals += sweep_history(workspace, live)
        except OSError:
            logger.warning("清理撤销副本失败：%s", workspace, exc_info=True)
    if removed_uploads or removed_loose or removed_journals:
        logger.info(
            "已清理遗留会话文件：上传目录 %s 个，落单附件 %s 个，撤销记录 %s 份",
            removed_uploads,
            removed_loose,
            removed_journals,
        )


def _discard_files(conversation_id: int, workspace: str | None) -> None:
    try:
        discard_uploads(conversation_id)
    except OSError:
        logger.warning("清理会话上传文件失败：%s", conversation_id, exc_info=True)
    if not workspace:
        return
    try:
        discard_journal(Path(workspace), conversation_id)
    except OSError:
        logger.warning("清理会话撤销副本失败：%s", conversation_id, exc_info=True)


def delete_conversation(db, conversation: Conversation) -> list[int]:
    rows = [conversation]
    release = []
    if (conversation.kind or "") == "group":
        for row in db.scalars(select(Conversation).where(Conversation.room_id == conversation.id)).all():
            if (row.kind or "chat") == "member":
                rows.append(row)
            else:
                release.append(row)
    removed = [(row.id, row.workspace) for row in rows]
    removed_ids = [row.id for row in rows]
    for row in rows:
        conversation_id = row.id
        db.query(Attachment).filter(Attachment.conversation_id == conversation_id).delete()
        db.query(Message).filter(Message.conversation_id == conversation_id).delete()
        db.query(Task).filter(Task.conversation_id == conversation_id).delete()
        db.query(Schedule).filter(Schedule.conversation_id == conversation_id).delete()
        db.delete(row)
    for row in release:
        name = row.member_name or ""
        row.room_id = None
        row.member_name = ""
        if (row.member_prompt or "") == _linked_prompt(name):
            row.member_prompt = ""
    db.commit()
    for conversation_id, workspace in removed:
        _discard_files(conversation_id, workspace)
    return removed_ids


_MEMBER_COLORS = ["#8d5a3c", "#e23b3b", "#f08a24", "#3b82f6", "#7c4dff", "#3cbe5a"]
_MEMBER_ICONS = ["blob", "squircle", "diamond", "circle", "star", "drop"]


def _group_mark(icon_key: str | None, icon_color: str | None) -> tuple[str, str]:
    key = (icon_key or "flock").strip()[:32] or "flock"
    color = (icon_color or "#f08a24").strip()[:16] or "#f08a24"
    return key, color


def _link_existing_group(db, title: str, conversation_ids: list[int], icon_key: str | None = None, icon_color: str | None = None) -> Conversation:
    chosen = []
    seen = set()
    for raw in conversation_ids:
        try:
            key = int(raw)
        except (TypeError, ValueError):
            raise ValueError("有会话不存在") from None
        if key in seen:
            raise ValueError("同一个会话不能加两次")
        seen.add(key)
        row = db.get(Conversation, key)
        uid = current_user_id()
        if row is None or (uid and row.user_id != uid):
            raise ValueError("有会话不存在")
        if (row.kind or "") == "group" or row.room_id or (row.kind or "") == "member":
            raise ValueError(f"{row.title or '这个会话'} 已经在群里")
        name = (row.title or "").strip()[:64]
        if not name or name in {"user", "用户"}:
            raise ValueError("会话名字不能作为群成员")
        chosen.append((row, name))
    if len(chosen) < 2:
        raise ValueError("群聊至少要拉进两个已有会话")
    names = [name for _row, name in chosen]
    if len(set(names)) != len(names):
        raise ValueError("这些会话的名字有重复，群里靠名字交接")
    stored = all_settings(db)
    group_title = (title or "").strip() or "、".join(names)
    group = Conversation(
        title=group_title[:255],
        preview="",
        provider=stored["provider"],
        model=stored["deepseek_model"] if stored["provider"] == "deepseek" else stored["ollama_model"],
        permission_mode=stored["permission_mode"],
        workspace=stored["workspace"],
        icon_key=_group_mark(icon_key, icon_color)[0],
        icon_color=_group_mark(icon_key, icon_color)[1],
        kind="group",
        **owner_kwargs(),
        room_state={
            "current_speaker": names[0],
            "current_turn": 0,
            "max_turns": 12,
            "member_ids": [row.id for row, _name in chosen],
        },
    )
    db.add(group)
    db.flush()
    for row, name in chosen:
        row.room_id = group.id
        row.member_name = name
        if not (row.member_prompt or "").strip():
            row.member_prompt = _linked_prompt(name)
    group.updated_at = datetime.now() + timedelta(seconds=2)
    db.commit()
    return group


def create_group(
    db,
    title: str,
    members: list[dict] | None = None,
    conversation_ids: list[int] | None = None,
    icon_key: str | None = None,
    icon_color: str | None = None,
) -> Conversation:
    if conversation_ids:
        return _link_existing_group(db, title, conversation_ids, icon_key, icon_color)
    members = members or []
    cleaned = []
    for item in members:
        name = str(item.get("name") or "").strip()
        duty = str(item.get("duty") or "").strip()
        if not name:
            continue
        cleaned.append({"name": name[:64], "duty": duty[:500], "icon_key": item.get("icon_key"), "icon_color": item.get("icon_color")})
    if len(cleaned) < 2:
        raise ValueError("群聊至少要有两个成员")
    names = [item["name"] for item in cleaned]
    if len(set(names)) != len(names):
        raise ValueError("成员名字不能重复")
    if any(name in {"user", "用户"} for name in names):
        raise ValueError("成员不能叫 user")
    stored = all_settings(db)
    group_title = (title or "").strip() or "、".join(names)
    mark, tint = _group_mark(icon_key, icon_color)
    group = Conversation(
        title=group_title[:255],
        preview="",
        provider=stored["provider"],
        model=stored["deepseek_model"] if stored["provider"] == "deepseek" else stored["ollama_model"],
        permission_mode=stored["permission_mode"],
        workspace=stored["workspace"],
        icon_key=mark,
        icon_color=tint,
        kind="group",
        **owner_kwargs(),
        room_state={"current_speaker": names[0], "current_turn": 0, "max_turns": 12, "member_ids": []},
    )
    db.add(group)
    db.flush()
    for index, item in enumerate(cleaned):
        prompt = f"你是{item['name']}。职责：{item['duty'] or '按自己的角色完成交办的任务。'}"
        member = Conversation(
            title=item["name"][:255],
            preview="你好，我已就位。",
            provider=group.provider,
            model=group.model,
            permission_mode=group.permission_mode,
            workspace=group.workspace,
            icon_key=(item["icon_key"] or _MEMBER_ICONS[index % len(_MEMBER_ICONS)])[:32],
            icon_color=(item["icon_color"] or _MEMBER_COLORS[index % len(_MEMBER_COLORS)])[:16],
            kind="member",
            **owner_kwargs(),
            room_id=group.id,
            member_name=item["name"],
            member_prompt=prompt,
        )
        db.add(member)
        db.flush()
        db.add(
            Message(
                conversation_id=member.id,
                role="assistant",
                content="你好，我已就位。等你或协调者给出具体安排。",
                speaker=item["name"],
                status="done",
            )
        )
        state = dict(group.room_state or {})
        ids = list(state.get("member_ids") or [])
        ids.append(member.id)
        state["member_ids"] = ids
        group.room_state = state
    group.updated_at = datetime.now() + timedelta(seconds=2)
    db.commit()
    return group


def private_threads(db, conversation: Conversation) -> list[dict]:
    if not conversation.room_id or not conversation.member_name or (conversation.kind or "") == "group":
        return []
    rows = db.scalars(
        select(Message)
        .where(
            Message.conversation_id == conversation.room_id,
            Message.channel == "private",
            or_(Message.speaker == conversation.member_name, Message.counterpart == conversation.member_name),
        )
        .order_by(Message.id.asc())
    ).all()
    buckets: dict[str, list] = {}
    order: list[str] = []
    for row in rows:
        other = row.counterpart if row.speaker == conversation.member_name else row.speaker
        if not other:
            continue
        if other not in buckets:
            buckets[other] = []
            order.append(other)
        buckets[other].append(
            {
                "id": row.id,
                "speaker": row.speaker or "",
                "counterpart": row.counterpart or "",
                "content": row.content or "",
                "created_at": row.created_at.isoformat(sep=" ", timespec="seconds") if row.created_at else "",
            }
        )
    return [{"counterpart": name, "count": len(buckets[name]), "messages": buckets[name]} for name in order]
