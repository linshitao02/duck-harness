"""把已连接的 MCP 服务发现成模型可以直接调用的工具。

公开名称是 mcp__<服务名>__<工具名>。发给服务器的仍是原始工具名。
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass, field

from . import mcp_client

MAX_NAME = 64
NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]")
GENERIC = {"list_mcp_tools", "call_mcp_tool"}
SERVER_TIMEOUT = 12
CACHE_SECONDS = 30


@dataclass
class ServerTools:
    key: str
    name: str
    instructions: str
    tools: list = field(default_factory=list)
    error: str = ""


@dataclass
class Snapshot:
    at: float
    key: str
    tools: list = field(default_factory=list)
    by_name: dict = field(default_factory=dict)
    servers: dict = field(default_factory=dict)
    prompt: str = "（没有接入 MCP 服务）"


_snapshots: dict[int, Snapshot] = {}


def _bucket() -> int:
    from ..access import current_user_id

    return current_user_id() or 0


def _current() -> Snapshot:
    return _snapshots.get(_bucket()) or Snapshot(at=0, key="")


def clear_snapshot() -> None:
    _snapshots.clear()
    mcp_client.discard_sessions()


def status_payload() -> dict:
    servers = []
    for state in _current().servers.values():
        servers.append(
            {
                "name": state.name,
                "ok": not state.error,
                "error": state.error,
                "tools": [tool.name for tool in state.tools],
            }
        )
    return {"servers": servers}


def public_tool_name(server: str, raw: str) -> str:
    joined = f"mcp__{server}__{raw}"
    normalized = NAME_CHARS.sub("_", joined)
    if normalized == joined and len(normalized) <= MAX_NAME:
        return normalized
    digest = hashlib.sha256(f"{server}\0{raw}".encode("utf-8")).hexdigest()[:12]
    return f"{normalized[: MAX_NAME - 13]}_{digest}"


def prompt_text() -> str:
    return _current().prompt or "（没有接入 MCP 服务）"


def bridged_tools() -> list:
    return list(_current().tools)


def find_tool(name: str):
    return _current().by_name.get(name)


def visible_bridged(allowed: list[str] | None) -> list:
    tools = bridged_tools()
    if not tools:
        return []
    if allowed is None or "call_mcp_tool" in allowed:
        return tools
    if "list_mcp_tools" in allowed:
        return [tool for tool in tools if tool.read_only]
    return [tool for tool in tools if tool.name in set(allowed)]


def _schema(raw) -> dict:
    if not isinstance(raw, dict):
        return {"type": "object", "properties": {}}
    schema = {key: value for key, value in raw.items() if key != "$schema"}
    schema.setdefault("type", "object")
    if not isinstance(schema.get("properties"), dict):
        schema["properties"] = {}
    return schema


def _required_text(schema: dict) -> str:
    required = schema.get("required") or []
    if not isinstance(required, list) or not required:
        names = list((schema.get("properties") or {}).keys())
        return "、".join(str(item) for item in names[:8]) or "无"
    return "、".join(str(item) for item in required[:8])


def _server_key(rows) -> str:
    payload = [
        {
            "id": row.id,
            "name": row.name,
            "command": row.command,
            "args": row.args or [],
            "env": row.env or {},
            "enabled": bool(row.enabled),
        }
        for row in rows
    ]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def _config_key(row) -> str:
    return row.name + "\n" + mcp_client.session_key(row.command, row.args or [], row.env or {})


async def _discover_server(row) -> tuple[list, str, str]:
    from .tools import Tool

    session = await mcp_client.connect(row.command, row.args or [], row.env or {})

    async def read_tools(client):
        return await client.list_tools()

    listed = await mcp_client.operate(row.command, row.args or [], row.env or {}, read_tools)
    instructions = str((session.info or {}).get("instructions") or "").strip()[:4000]
    made = []
    seen = set()
    for item in listed:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        raw = str(item["name"])
        if raw in seen:
            continue
        seen.add(raw)
        public = public_tool_name(str(row.name), raw)
        schema = _schema(item.get("inputSchema"))
        annotations = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
        read_only = bool(annotations.get("readOnlyHint"))
        description = str(item.get("description") or "").strip() or f"MCP 服务 {row.name} 的 {raw}"

        async def call(args, ctx, server_name=str(row.name), tool_name=raw):
            return await _invoke(server_name, tool_name, args, ctx)

        made.append(
            Tool(
                public,
                description,
                schema,
                call,
                read_only,
            )
        )
    return made, instructions, ""


async def _invoke(server_name: str, tool_name: str, arguments: dict, ctx) -> "ToolResult":
    from .tools import ToolResult, _servers

    row = next((item for item in await _servers(ctx.db) if item.name == server_name), None)
    if row is None:
        return ToolResult(False, f"MCP 服务 {server_name} 已不在配置里")
    payload = arguments if isinstance(arguments, dict) else {}
    try:
        result = await mcp_client.operate(
            row.command,
            row.args or [],
            row.env or {},
            lambda client: client.request("tools/call", {"name": tool_name, "arguments": payload}),
        )
    except Exception as exc:
        return ToolResult(False, f"MCP 调用失败: {exc}")
    text = _result_text(result, tool_name)
    if result.get("isError") is True:
        return ToolResult(False, text or f"{tool_name} 执行失败")
    return ToolResult(True, text or "（无输出）")


def _result_text(result: dict, tool_name: str) -> str:
    chunks = []
    for block in result.get("content") or []:
        if isinstance(block, str):
            chunks.append(block)
            continue
        if not isinstance(block, dict):
            continue
        if block.get("text"):
            chunks.append(str(block["text"]))
        elif block.get("type") == "image":
            chunks.append(f"（{tool_name} 返回了一张图片，类型 {block.get('mimeType') or '未知'}）")
        elif block.get("uri") or block.get("name"):
            chunks.append(f"资源 {block.get('name') or ''} {block.get('uri') or ''}".strip())
    if chunks:
        return "\n".join(chunks)
    structured = result.get("structuredContent")
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False)
    return ""


def _prompt(rows: list[tuple], errors: list[str]) -> str:
    visible = [item for item in rows if item[2]]
    if not visible and not errors:
        return "（没有接入 MCP 服务）"
    lines = []
    many = sum(len(tools) for _, _, tools in visible) > 16
    if many:
        lines.append("MCP 工具较多，没有全部放进工具列表。用 tool_search 按关键词查找，再用 call_mcp_tool 调用。")
        for server, instructions, tools in visible:
            lines.append(f"### MCP server: {server}（{len(tools)} 个工具）")
            if instructions:
                lines.append(instructions)
        for error in errors:
            lines.append(error)
        return "\n".join(lines)[:8000]
    if visible:
        lines.extend(
            [
                "这些工具已经出现在你的工具列表里。名称就是调用名，参数按各自的 schema 填写。",
                "任务和描述对得上时直接调用，不要再用 list_mcp_tools 或 call_mcp_tool 转发。",
                "资源仍用 list_mcp_resources 和 read_mcp_resource。要停用某个服务，拒绝对应的 mcp__服务名。",
            ]
        )
    for server, instructions, tools in rows:
        lines.append(f"### MCP server: {server}")
        if instructions:
            lines.append(instructions)
        for tool in tools:
            lines.append(f"- {tool.name}：{tool.description}。参数：{_required_text(tool.parameters)}")
    for error in errors:
        lines.append(error)
    text = "\n".join(lines)
    return text[:8000]


async def refresh(db, force: bool = False) -> None:
    from .tools import _servers

    bucket = _bucket()
    current = _snapshots.get(bucket) or Snapshot(at=0, key="")
    rows = list(await _servers(db))
    key = _server_key(rows)
    if not force and current.key == key and time.time() - current.at < CACHE_SECONDS:
        return
    import asyncio

    found = []
    errors = []
    sections = []
    states: dict[int, ServerTools] = {}
    previous = current.servers
    for row in rows:
        config_key = _config_key(row)
        try:
            tools, instructions, _note = await asyncio.wait_for(_discover_server(row), timeout=SERVER_TIMEOUT)
        except Exception as exc:
            await mcp_client.drop(mcp_client.session_key(row.command, row.args or [], row.env or {}))
            old = previous.get(row.id)
            if old is not None and old.key == config_key and old.tools:
                message = f"MCP 服务 {row.name} 暂时连不上，仍使用上次的 {len(old.tools)} 个工具：{exc}"
                errors.append(message)
                states[row.id] = ServerTools(config_key, row.name, old.instructions, list(old.tools), message)
                sections.append((row.name, old.instructions, list(old.tools)))
                found.extend(old.tools)
            else:
                message = f"MCP 服务 {row.name} 连接失败：{exc}"
                errors.append(message)
                states[row.id] = ServerTools(config_key, row.name, "", [], message)
            continue
        state = ServerTools(config_key, row.name, instructions, tools, "")
        states[row.id] = state
        sections.append((row.name, instructions, tools))
        found.extend(tools)
    await mcp_client.prune({mcp_client.session_key(row.command, row.args or [], row.env or {}) for row in rows})
    by_name = {}
    unique = []
    for tool in found:
        if tool.name in by_name:
            errors.append(f"工具名 {tool.name} 重复，已保留先发现的那个")
            continue
        by_name[tool.name] = tool
        unique.append(tool)
    kept = set(by_name)
    sections = [
        (server, instructions, [tool for tool in tools if tool.name in kept])
        for server, instructions, tools in sections
    ]
    _snapshots[bucket] = Snapshot(
        at=time.time(),
        key=key,
        tools=unique,
        by_name=by_name,
        servers=states,
        prompt=_prompt(sections, errors),
    )
