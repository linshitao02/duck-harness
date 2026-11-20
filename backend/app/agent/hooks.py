"""工作区钩子。支持执行前、执行后、失败后和停止。没有 hooks.json 就不做任何事。"""

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .safety import blocked_command

MAX_HOOK_CHARS = 4000
EVENT_KEYS = {
    "pre_tool": ("pre_tool", "PreToolUse"),
    "post_tool": ("post_tool", "PostToolUse"),
    "post_tool_failure": ("post_tool_failure", "PostToolUseFailure"),
    "stop": ("stop", "Stop"),
}


@dataclass
class HookEffect:
    block: str = ""
    allow: bool = False
    ask: bool = False
    updated: dict | None = None
    extra: str = ""
    notes: list[str] = field(default_factory=list)


def _load(workspace: Path) -> dict:
    path = workspace / ".harness" / "hooks.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"__invalid__": True}
    return data if isinstance(data, dict) else {"__invalid__": True}


def _rules(config: dict, event: str) -> list[dict]:
    if config.get("__invalid__"):
        return []
    found = []
    for key in EVENT_KEYS[event]:
        rows = config.get(key) or []
        if isinstance(rows, list):
            found.extend(item for item in rows if isinstance(item, dict))
    return found


def _matches(rule: dict, name: str) -> bool:
    tools = rule.get("tools") or []
    if tools and name not in tools:
        return False
    matcher = str(rule.get("matcher") or "").strip()
    if not matcher:
        return True
    try:
        return re.search(matcher, name) is not None
    except re.error:
        return matcher == name


def _interpret(stdout: str, stderr: str, code: int | None, event: str) -> HookEffect:
    effect = HookEffect()
    text = (stdout or "").strip()
    payload = None
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
    failed = code not in (0, None)
    if isinstance(payload, dict):
        specific = payload.get("hookSpecificOutput")
        if not isinstance(specific, dict):
            specific = {}
        decision = str(payload.get("decision") or "").lower()
        permission = str(specific.get("permissionDecision") or "").lower()
        reason = str(specific.get("permissionDecisionReason") or payload.get("reason") or "").strip()
        extra = str(specific.get("additionalContext") or payload.get("additionalContext") or "").strip()
        updated = specific.get("updatedInput")
        if isinstance(updated, dict):
            effect.updated = updated
        if decision == "block" or permission == "deny":
            effect.block = (reason or stderr or text or "钩子拒绝了这次操作").strip()[:MAX_HOOK_CHARS]
        elif permission == "allow" or decision == "approve":
            effect.allow = True
        elif permission == "ask":
            effect.ask = True
        if extra:
            effect.extra = extra[:MAX_HOOK_CHARS]
        if event in {"pre_tool", "stop"} and failed and not effect.block:
            effect.block = (reason or stderr or text or "钩子拒绝了这次操作").strip()[:MAX_HOOK_CHARS]
        if event in {"post_tool", "post_tool_failure"}:
            if failed and not effect.extra:
                effect.extra = (reason or stderr or text or "钩子没有通过").strip()[:MAX_HOOK_CHARS]
            effect.block = ""
        return effect
    if event in {"pre_tool", "stop"} and failed:
        effect.block = (stderr or stdout or "钩子拒绝了这次操作").strip()[:MAX_HOOK_CHARS]
    elif event in {"post_tool", "post_tool_failure"} and (failed or text):
        effect.extra = (stderr or stdout or "钩子没有通过").strip()[:MAX_HOOK_CHARS] if failed else text[:MAX_HOOK_CHARS]
    return effect


async def _run_command(workspace: Path, command: str, payload: dict) -> tuple[str, str, int | None]:
    encoded = json.dumps(payload, ensure_ascii=False)[:8000]
    wrapped = (
        "$env:HARNESS_EVENT=" + _ps_quote(str(payload.get("event") or "")) + "; "
        "$env:HARNESS_TOOL=" + _ps_quote(str(payload.get("tool") or "")) + "; "
        "$env:HARNESS_ARGS=" + _ps_quote(encoded) + "; "
        + command
    )
    proc = await asyncio.create_subprocess_exec(
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        wrapped,
        cwd=str(workspace),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(raw), timeout=10)
    except TimeoutError:
        proc.kill()
        return "", "钩子超时", 1
    return (
        _decode(stdout),
        _decode(stderr),
        proc.returncode,
    )


def _decode(data: bytes) -> str:
    return (data or b"").decode("utf-8", errors="replace").strip()


async def run_event(workspace: Path, event: str, name: str, payload: dict) -> HookEffect:
    config = _load(workspace)
    if config.get("__invalid__") and event == "pre_tool":
        return HookEffect(block="钩子文件无法解析，已拦住这次工具调用")
    combined = HookEffect()
    for rule in _rules(config, event):
        if name and not _matches(rule, name):
            continue
        command = str(rule.get("command") or "").strip()
        if not command:
            continue
        blocked = blocked_command(command)
        if blocked:
            if event == "pre_tool":
                combined.block = blocked
                return combined
            continue
        try:
            stdout, stderr, code = await _run_command(workspace, command, payload)
        except OSError as exc:
            if event == "pre_tool":
                combined.block = f"钩子无法执行: {exc}"
                return combined
            continue
        effect = _interpret(stdout, stderr, code, event)
        if effect.updated and combined.updated is None:
            combined.updated = effect.updated
        if effect.extra:
            combined.notes.append(effect.extra)
        if effect.allow:
            combined.allow = True
        if effect.ask:
            combined.ask = True
        if effect.block:
            combined.block = effect.block
            if event in {"pre_tool", "stop"}:
                return combined
    if combined.notes:
        combined.extra = "\n".join(combined.notes)[:MAX_HOOK_CHARS]
    return combined


async def before_tool(workspace: Path, name: str, arguments: dict) -> HookEffect:
    return await run_event(
        workspace,
        "pre_tool",
        name,
        {"event": "PreToolUse", "tool": name, "arguments": arguments},
    )


async def after_tool(workspace: Path, name: str, arguments: dict, ok: bool, text: str) -> str:
    event = "post_tool" if ok else "post_tool_failure"
    effect = await run_event(
        workspace,
        event,
        name,
        {
            "event": "PostToolUse" if ok else "PostToolUseFailure",
            "tool": name,
            "arguments": arguments,
            "ok": ok,
            "output": (text or "")[:4000],
        },
    )
    return effect.extra


async def stop_feedback(workspace: Path, assistant_text: str) -> str:
    effect = await run_event(
        workspace,
        "stop",
        "",
        {"event": "Stop", "assistant": (assistant_text or "")[:4000]},
    )
    return effect.block


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
