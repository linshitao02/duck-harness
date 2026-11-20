"""裁掉较早的工具结果。完整原文仍留在数据库里，只缩短送给模型的副本。"""

CLEARED = "[旧工具结果已裁剪]"
COMPACTABLE = {
    "read_file",
    "bash",
    "grep",
    "glob_files",
    "web_search",
    "web_fetch",
    "edit_file",
    "write_file",
    "list_dir",
}
KEEP_RECENT = 4
TRIGGER_COUNT = 8
TRIGGER_CHARS = 24_000
FLOOR = 2


def _tool_name(call: dict) -> str:
    function = call.get("function") or {}
    return str(function.get("name") or call.get("name") or "")


def _content_text(content) -> str:
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
        return "".join(parts)
    return str(content or "")


def shrink_tool_results(messages: list[dict]) -> list[dict]:
    order: list[str] = []
    compactable: set[str] = set()
    for message in messages:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            name = _tool_name(call)
            call_id = str(call.get("id") or "")
            if call_id and (name in COMPACTABLE or name.startswith("mcp__")):
                compactable.add(call_id)
                order.append(call_id)
    if not order:
        return messages
    bodies = {}
    for message in messages:
        if message.get("role") == "tool" and message.get("tool_call_id") in compactable:
            bodies[message["tool_call_id"]] = _content_text(message.get("content"))
    present = [call_id for call_id in order if call_id in bodies and bodies[call_id] != CLEARED]
    total = sum(len(bodies[call_id]) for call_id in present)
    if len(present) <= TRIGGER_COUNT and total <= TRIGGER_CHARS:
        return messages
    keep = present[-KEEP_RECENT:]
    while len(keep) > FLOOR and sum(len(bodies[call_id]) for call_id in keep) > TRIGGER_CHARS:
        keep = keep[1:]
    clear = set(present) - set(keep)
    if not clear:
        return messages
    for index, message in enumerate(messages):
        if message.get("role") == "tool" and message.get("tool_call_id") in clear:
            messages[index] = {**message, "content": CLEARED}
    return messages
