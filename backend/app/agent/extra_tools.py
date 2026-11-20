"""计划模式、提问、笔记本、待办、后台任务、定时任务、MCP 资源和 git 工作树。"""

import asyncio
import json
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from ..models import Conversation, Schedule, Task
from . import mcp_client
from .state import JOBS, PLAN_RETURN
from .tools import (
    Tool,
    ToolContext,
    ToolResult,
    _object,
    _prop,
    _servers,
    clip,
    guard_path,
    plan_display,
    plan_file,
    resolve_path,
)


async def _persist_mode(ctx: ToolContext, mode: str) -> None:
    ctx.permission_mode = mode
    if ctx.conversation_id:
        row = ctx.db.get(Conversation, ctx.conversation_id)
        if row is not None:
            row.permission_mode = mode
            ctx.db.commit()
    if ctx.emit:
        await ctx.emit({"type": "mode", "permission_mode": mode})


async def _ask_user(args: dict, ctx: ToolContext) -> ToolResult:
    questions = args.get("questions") or []
    if not isinstance(questions, list) or not questions:
        return ToolResult(False, "需要 questions 数组")
    cleaned = []
    for item in questions:
        if not isinstance(item, dict):
            return ToolResult(False, "每个问题都要是对象")
        prompt = str(item.get("question") or "").strip()
        options = item.get("options") or []
        if not prompt or not isinstance(options, list) or len(options) < 2:
            return ToolResult(False, "每个问题至少要有题干和两个选项")
        cleaned.append(
            {
                "question": prompt,
                "multiSelect": bool(item.get("multiSelect") or item.get("multi_select")),
                "options": [
                    {
                        "label": str(option.get("label") or "").strip(),
                        "description": str(option.get("description") or "").strip(),
                    }
                    for option in options
                    if isinstance(option, dict) and str(option.get("label") or "").strip()
                ],
            }
        )
        if len(cleaned[-1]["options"]) < 2:
            return ToolResult(False, "选项缺少 label")
    if ctx.depth:
        return ToolResult(False, "子代理不能直接向用户提问")
    answers = await ctx.broker.ask({"questions": cleaned, "emit": ctx.emit, "timeout": 180})
    if not answers:
        return ToolResult(False, "用户没有回答")
    return ToolResult(True, json.dumps(answers, ensure_ascii=False))


async def _enter_plan_mode(args: dict, ctx: ToolContext) -> ToolResult:
    if ctx.depth:
        return ToolResult(False, "子代理不能切换主会话的计划模式")
    if ctx.permission_mode == "plan":
        return ToolResult(True, f"已经在计划模式。请把方案写到 {plan_display(ctx)}")
    key = ctx.conversation_id or 0
    PLAN_RETURN[key] = ctx.permission_mode or "auto"
    await _persist_mode(ctx, "plan")
    return ToolResult(
        True,
        "已进入计划模式。现在只能查看，以及写入 "
        f"{plan_display(ctx)}。看清楚后再调用 exit_plan_mode 请用户批准。",
    )


async def _exit_plan_mode(args: dict, ctx: ToolContext) -> ToolResult:
    if ctx.depth:
        return ToolResult(False, "子代理不能退出计划模式")
    if ctx.permission_mode != "plan":
        return ToolResult(False, "当前不是计划模式")
    path = plan_file(ctx)
    supplied = args.get("plan")
    if supplied:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(supplied), encoding="utf-8")
    if not path.is_file():
        return ToolResult(False, f"还没有计划文件。请先把方案写入 {path}")
    plan = path.read_text(encoding="utf-8", errors="replace").strip()
    if not plan:
        return ToolResult(False, "计划文件是空的")
    approved = await ctx.broker.request(
        {
            "name": "exit_plan_mode",
            "arguments": {"path": str(path)},
            "preview": plan[:4000],
            "emit": ctx.emit,
            "timeout": 180,
        }
    )
    if not approved:
        return ToolResult(False, "用户还没有批准计划，仍然停留在计划模式")
    key = ctx.conversation_id or 0
    restore = PLAN_RETURN.pop(key, "auto")
    if restore == "plan":
        restore = "auto"
    await _persist_mode(ctx, restore)
    return ToolResult(True, f"计划已批准，权限回到 {restore}。可以按 {path} 开始改代码。")


def _cell_source(cell: dict) -> str:
    source = cell.get("source") or ""
    if isinstance(source, list):
        return "".join(source)
    return str(source)


def _set_source(cell: dict, source: str) -> None:
    cell["source"] = source.splitlines(keepends=True) or [""]


def _find_cell(cells: list, cell_id: str | None):
    if cell_id is None or cell_id == "":
        return None
    for index, cell in enumerate(cells):
        if str(cell.get("id") or "") == str(cell_id) or str(index) == str(cell_id):
            return index
    return None


async def _notebook_edit(args: dict, ctx: ToolContext) -> ToolResult:
    from .edits import remember_read, require_fresh_read

    path = resolve_path(ctx.workspace, str(args.get("notebook_path") or args.get("path") or ""))
    reason = guard_path(path)
    if reason:
        return ToolResult(False, reason)
    if path.suffix.lower() != ".ipynb":
        return ToolResult(False, "notebook_edit 只能改 .ipynb")
    if not path.is_file():
        return ToolResult(False, f"文件不存在: {path}")
    from .diffutil import unified
    from .history import remember_change

    stale = require_fresh_read(path)
    if stale:
        return ToolResult(False, stale)
    try:
        notebook = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return ToolResult(False, f"笔记本不是合法 JSON: {exc}")
    cells = notebook.setdefault("cells", [])
    mode = str(args.get("edit_mode") or "replace")
    cell_id = args.get("cell_id")
    new_source = str(args.get("new_source") or "")
    cell_type = args.get("cell_type")
    if mode == "insert":
        if cell_type not in {"code", "markdown"}:
            return ToolResult(False, "插入单元格时 cell_type 必须是 code 或 markdown")
        cell = {"cell_type": cell_type, "id": uuid.uuid4().hex[:8], "metadata": {}, "source": []}
        if cell_type == "code":
            cell["outputs"] = []
            cell["execution_count"] = None
        _set_source(cell, new_source)
        index = _find_cell(cells, None if cell_id in (None, "") else str(cell_id))
        cells.insert(0 if index is None else index + 1, cell)
        touched = cell["id"]
    elif mode == "delete":
        index = _find_cell(cells, None if cell_id is None else str(cell_id))
        if index is None:
            return ToolResult(False, "没有这个单元格")
        touched = str(cells[index].get("id") or index)
        cells.pop(index)
        cell_type = cell_type or "code"
    else:
        index = _find_cell(cells, None if cell_id is None else str(cell_id))
        if index is None:
            return ToolResult(False, "没有这个单元格。cell_id 用单元格 id 或从 0 开始的序号")
        cell = cells[index]
        if cell_type in {"code", "markdown"}:
            cell["cell_type"] = cell_type
        elif cell_type:
            return ToolResult(False, "cell_type 只能是 code 或 markdown")
        _set_source(cell, new_source)
        touched = str(cell.get("id") or index)
        cell_type = cell.get("cell_type")
    before = path.read_text(encoding="utf-8")
    raw = json.dumps(notebook, ensure_ascii=False, indent=1)
    remember_change(ctx, path)
    path.write_text(raw, encoding="utf-8")
    remember_read(path, raw, False)
    return ToolResult(
        True,
        f"已{mode}单元格 {touched}（{cell_type or 'code'}）: {path}",
        diff=unified(path.name, before, raw),
    )


async def _todo_write(args: dict, ctx: ToolContext) -> ToolResult:
    items = args.get("todos")
    if not isinstance(items, list) or not items:
        return ToolResult(False, "需要 todos 数组")
    if not ctx.conversation_id:
        return ToolResult(False, "没有会话，不能写待办")
    merge = args.get("merge", True)
    if isinstance(merge, str):
        merge = merge.strip().lower() not in {"0", "false", "no"}
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            return ToolResult(False, "todos 里的每一项都要是对象")
        title = str(item.get("content") or item.get("title") or "").strip()
        status = str(item.get("status") or "pending")
        if status == "completed":
            status = "done"
        if status not in {"pending", "in_progress", "done", "cancelled"}:
            return ToolResult(False, "status 只能是 pending、in_progress、done、cancelled")
        if not title and not item.get("id"):
            return ToolResult(False, "待办需要 content")
        normalized.append((item.get("id"), title, str(item.get("activeForm") or item.get("detail") or ""), status))
    rows = ctx.db.scalars(select(Task).where(Task.conversation_id == ctx.conversation_id).order_by(Task.id.asc())).all()
    if not merge:
        for row in rows:
            ctx.db.delete(row)
        rows = []
    by_id = {row.id: row for row in rows}
    for task_id, title, detail, status in normalized:
        row = by_id.get(int(task_id)) if task_id else None
        if row is None and title:
            row = next((item for item in rows if item.title == title), None)
        if row is None:
            if not title:
                return ToolResult(False, "找不到要更新的待办")
            row = Task(conversation_id=ctx.conversation_id, title=title, detail=detail, status=status)
            ctx.db.add(row)
            rows.append(row)
        else:
            if title:
                row.title = title
            if detail:
                row.detail = detail
            row.status = status
            row.updated_at = datetime.now()
    ctx.db.flush()
    active = [row for row in rows if row.status == "in_progress"]
    if len(active) > 1:
        ctx.db.rollback()
        return ToolResult(False, "同一时间只能有一个 in_progress")
    ctx.db.commit()
    listed = ctx.db.scalars(select(Task).where(Task.conversation_id == ctx.conversation_id).order_by(Task.id.asc())).all()
    return ToolResult(True, "\n".join(f"#{row.id} [{row.status}] {row.title}" for row in listed) or "待办已清空")


def _conversation_token(conversation_id: int | None) -> str:
    try:
        return str(int(conversation_id)) if conversation_id is not None else "0"
    except (TypeError, ValueError):
        return "0"


def _worktree_slug(raw: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in raw)[:40].strip(".-")
    return cleaned or "task"


def _own_job(ctx: ToolContext, job_id: str) -> dict | None:
    from .state import same_conversation

    job = JOBS.get(job_id)
    if job is None or not same_conversation(job, ctx):
        return None
    return job


async def _task_output(args: dict, ctx: ToolContext) -> ToolResult:
    job_id = str(args.get("id") or args.get("task_id") or args.get("job_id") or args.get("shell_id") or "").lstrip("#").strip()
    job = _own_job(ctx, job_id)
    if job is None:
        return ToolResult(False, "没有这个后台任务")
    if not job.get("done"):
        return ToolResult(True, f"#{job_id} 仍在运行\n{job.get('command')}")
    body = job.get("text") or "（无输出）"
    return ToolResult(job.get("code") == 0, clip(f"退出码 {job.get('code')}\n{body}"))


async def _task_stop(args: dict, ctx: ToolContext) -> ToolResult:
    job_id = str(args.get("id") or args.get("task_id") or args.get("job_id") or args.get("shell_id") or "").lstrip("#").strip()
    job = _own_job(ctx, job_id)
    if job is None:
        return ToolResult(False, "没有这个后台任务")
    proc = job.get("proc")
    if proc is not None and proc.returncode is None:
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3)
        except TimeoutError:
            pass
    job["done"] = True
    job["code"] = proc.returncode if proc is not None else -1
    return ToolResult(True, f"已停止 #{job_id}")


async def _list_schedules(args: dict, ctx: ToolContext) -> ToolResult:
    rows = ctx.db.scalars(select(Schedule).order_by(Schedule.id.asc())).all()
    if ctx.conversation_id:
        rows = [row for row in rows if row.conversation_id == ctx.conversation_id]
    if not rows:
        return ToolResult(True, "没有定时任务")
    lines = []
    for row in rows:
        when = row.next_run.isoformat(sep=" ", timespec="seconds") if row.next_run else "未安排"
        state = "启用" if row.enabled else "停用"
        lines.append(f"#{row.id} [{state}] 下次 {when} 每 {row.every_seconds} 秒\n{row.prompt}")
    return ToolResult(True, "\n\n".join(lines))


async def _cancel_schedule(args: dict, ctx: ToolContext) -> ToolResult:
    row = ctx.db.get(Schedule, int(args.get("id") or 0))
    if row is None or (ctx.conversation_id is not None and row.conversation_id != ctx.conversation_id):
        return ToolResult(False, "没有这个定时任务")
    row.enabled = False
    row.next_run = None
    ctx.db.commit()
    return ToolResult(True, f"已停用定时任务 #{row.id}")


async def _list_mcp_resources(args: dict, ctx: ToolContext) -> ToolResult:
    rows = await _servers(ctx.db)
    if not rows:
        return ToolResult(True, "还没有启用的 MCP 服务")
    chunks = []
    for row in rows:
        try:
            resources = await mcp_client.operate(
                row.command,
                row.args or [],
                row.env or {},
                lambda client: client.list_resources(),
            )
            if not resources:
                chunks.append(f"{row.name}: （无资源）")
                continue
            lines = [f"{row.name}:"]
            for item in resources:
                lines.append(f"- {item.get('name') or item.get('uri')} {item.get('uri') or ''}".rstrip())
            chunks.append("\n".join(lines))
        except Exception as exc:
            chunks.append(f"{row.name}: 列出资源失败 {exc}")
    return ToolResult(True, "\n\n".join(chunks))


async def _read_mcp_resource(args: dict, ctx: ToolContext) -> ToolResult:
    server_name = str(args.get("server") or "").strip()
    uri = str(args.get("uri") or "").strip()
    if not server_name or not uri:
        return ToolResult(False, "需要 server 和 uri")
    row = next((item for item in await _servers(ctx.db) if item.name == server_name), None)
    if row is None:
        return ToolResult(False, "没有这个 MCP 服务")
    try:
        text = await mcp_client.operate(
            row.command,
            row.args or [],
            row.env or {},
            lambda client, uri=uri: client.read_resource(uri),
        )
        return ToolResult(True, clip(text))
    except Exception as exc:
        return ToolResult(False, f"读取资源失败: {exc}")


async def _git(ctx: ToolContext, *argv: str) -> tuple[int, str]:
    git = shutil.which("git")
    if not git:
        return 127, "没有找到 git"
    root = ctx.origin_workspace or ctx.workspace
    proc = await asyncio.create_subprocess_exec(
        git,
        *argv,
        cwd=str(root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        proc.kill()
        return 124, "git 超时"
    text = (stdout or stderr).decode("utf-8", errors="replace").strip()
    return proc.returncode or 0, text


async def _enter_worktree(args: dict, ctx: ToolContext) -> ToolResult:
    if ctx.origin_workspace is None:
        ctx.origin_workspace = ctx.workspace
    code, text = await _git(ctx, "rev-parse", "--is-inside-work-tree")
    if code != 0:
        return ToolResult(False, "当前工作区不是 git 仓库，不能建工作树")
    name = _worktree_slug(str(args.get("name") or "task"))
    target = ctx.origin_workspace / ".harness" / "worktrees" / _conversation_token(ctx.conversation_id) / name
    if target.exists():
        ctx.workspace = target
        return ToolResult(True, f"工作树已存在，已切到 {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    code, text = await _git(ctx, "worktree", "add", "-b", f"harness/{_conversation_token(ctx.conversation_id)}/{name}", str(target), "HEAD")
    if code != 0:
        return ToolResult(False, text or "创建工作树失败")
    ctx.workspace = target
    return ToolResult(True, f"已进入工作树 {target}。之后的文件和命令都在这里，完成后再 exit_worktree。")


async def _exit_worktree(args: dict, ctx: ToolContext) -> ToolResult:
    origin = ctx.origin_workspace
    if origin is None or ctx.workspace.resolve() == origin.resolve():
        return ToolResult(True, "当前就在主工作区")
    leaving = ctx.workspace
    ctx.workspace = origin
    code, text = await _git(ctx, "worktree", "remove", "--force", str(leaving))
    if code != 0:
        return ToolResult(True, f"已回到主工作区。移除工作树时 git 说：{text}")
    return ToolResult(True, f"已回到主工作区，并移除 {leaving}")


async def _tool_search(args: dict, ctx: ToolContext) -> ToolResult:
    query = str(args.get("query") or "").strip().lower()
    if not query:
        return ToolResult(False, "缺少 query")
    from .mcp_bridge import bridged_tools

    hits = []
    for tool in bridged_tools():
        blob = f"{tool.name} {tool.description}".lower()
        if query in blob or any(part in blob for part in query.split()):
            schema = json.dumps(tool.parameters, ensure_ascii=False)
            hits.append(f"{tool.name}: {tool.description}\n参数: {schema}")
        if len(hits) >= 8:
            break
    if not hits:
        return ToolResult(True, "没有匹配的 MCP 工具。可以换个关键词，或用 list_mcp_tools。")
    return ToolResult(True, "匹配到这些工具，用对应的 mcp__ 名字或 call_mcp_tool 调用：\n" + "\n".join(hits))


async def _send_message(args: dict, ctx: ToolContext) -> ToolResult:
    target = str(args.get("to") or "").strip()
    text = str(args.get("text") or args.get("message") or "").strip()
    if not target or not text:
        return ToolResult(False, "需要 to 和 text")
    from .loop import send_mail

    send_mail(target, text)
    return ToolResult(True, f"已送到子代理 {target}。对方在下一步或 resume 时会读到。")


async def _lsp(args: dict, ctx: ToolContext) -> ToolResult:
    from .lsp_client import server_for, session_for

    action = str(args.get("action") or "definition").strip().lower().replace("-", "_")
    path = resolve_path(ctx.workspace, str(args.get("path") or ""))
    reason = guard_path(path)
    if reason:
        return ToolResult(False, reason)
    if not path.is_file():
        return ToolResult(False, f"文件不存在: {path}")
    if server_for(path) is None:
        return ToolResult(False, f"没有为 {path.suffix or '该文件'} 配置语言服务")
    line = max(1, int(args.get("line") or 1))
    character = max(1, int(args.get("character") or 1))
    root = ctx.origin_workspace or ctx.workspace
    try:
        session = await session_for(root, path)
        position = {"line": line - 1, "character": character - 1}
        document = {"uri": path.resolve().as_uri()}
        located = {"textDocument": document, "position": position}
        async with session.lock:
            if action == "references":
                result = await session.request(
                    "textDocument/references",
                    {**located, "context": {"includeDeclaration": True}},
                )
            elif action == "diagnostics":
                result = await session.request("textDocument/diagnostic", {"textDocument": document})
            elif action == "hover":
                result = await session.request("textDocument/hover", located)
            elif action == "document_symbol":
                result = await session.request("textDocument/documentSymbol", {"textDocument": document})
            elif action == "workspace_symbol":
                result = await session.request("workspace/symbol", {"query": str(args.get("query") or "")})
            elif action == "implementation":
                result = await session.request("textDocument/implementation", located)
            elif action == "prepare_call_hierarchy":
                result = await session.request("textDocument/prepareCallHierarchy", located)
            elif action in {"incoming_calls", "outgoing_calls"}:
                item = args.get("item")
                if not isinstance(item, dict):
                    prepared = await session.request("textDocument/prepareCallHierarchy", located)
                    if isinstance(prepared, list) and prepared:
                        item = prepared[0]
                    else:
                        return ToolResult(True, "这个位置没有调用层次")
                method = "callHierarchy/incomingCalls" if action == "incoming_calls" else "callHierarchy/outgoingCalls"
                result = await session.request(method, {"item": item})
            elif action == "definition":
                result = await session.request("textDocument/definition", located)
            else:
                return ToolResult(
                    False,
                    "action 只能是 definition、references、diagnostics、hover、document_symbol、workspace_symbol、implementation、prepare_call_hierarchy、incoming_calls、outgoing_calls",
                )
    except RuntimeError as exc:
        return ToolResult(False, str(exc))
    return ToolResult(True, clip(json.dumps(result, ensure_ascii=False, default=str)[:8000]))


async def _sleep(args: dict, ctx: ToolContext) -> ToolResult:
    seconds = int(args.get("seconds") or 1)
    seconds = max(0, min(seconds, 10))
    await asyncio.sleep(seconds)
    return ToolResult(True, f"已等待 {seconds} 秒")


def register(registry: list) -> None:
    registry.extend(
        [
            Tool(
                "ask_user",
                "向用户提一个或多个选择题。每个问题至少两个选项，需要多选时把 multiSelect 设为 true。推荐项放第一个并在标签末尾写（推荐）。计划还没写完时用它澄清需求，不要用它询问计划是否通过。",
                _object(
                    {
                        "questions": {
                            "type": "array",
                            "description": "要问用户的选择题。每个问题至少两个选项。",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "question": _prop("string", "问题本身。"),
                                    "multiSelect": _prop("boolean", "为 true 时允许多选。默认单选。"),
                                    "options": {
                                        "type": "array",
                                        "description": "选项。推荐项放第一个，并在标签末尾写（推荐）。",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "label": _prop("string", "选项标签。"),
                                                "description": _prop("string", "这个选项的补充说明。"),
                                            },
                                            "required": ["label"],
                                        },
                                    },
                                },
                                "required": ["question", "options"],
                            },
                        }
                    },
                    ["questions"],
                ),
                _ask_user,
                True,
            ),
            Tool(
                "enter_plan_mode",
                "任务需要先设计再改代码时进入计划模式。进入后只能查看，并把方案写到当前会话自己的 .harness/plans/{会话号}.md。",
                _object({}, []),
                _enter_plan_mode,
                True,
            ),
            Tool(
                "exit_plan_mode",
                "计划已经写进当前会话自己的计划文件后调用，请用户批准。批准前不要改业务代码。也可以用 plan 参数把方案直接写入该文件。",
                _object({"plan": _prop("string", "要写入当前会话计划文件的方案。不填则使用已经写好的文件。")}, []),
                _exit_plan_mode,
            ),
            Tool(
                "notebook_edit",
                "修改 Jupyter 笔记本的单元格。edit_mode 可以是 replace、insert、delete。cell_id 是单元格 id 或从 0 开始的序号。改之前要先 read_file。",
                _object(
                    {
                        "notebook_path": _prop("string", "笔记本路径，用工作区相对路径或绝对路径。"),
                        "cell_id": _prop("string", "单元格 id，或从 0 开始的序号。insert 时新单元格放在它后面；不填则插在开头。"),
                        "new_source": _prop("string", "单元格的新内容。"),
                        "cell_type": _prop("string", "code 或 markdown。不填则保持原类型。insert 时必须填写。", enum=["code", "markdown"]),
                        "edit_mode": _prop("string", "replace、insert 或 delete。默认 replace。", enum=["replace", "insert", "delete"]),
                    },
                    ["notebook_path", "new_source"],
                ),
                _notebook_edit,
            ),
            Tool(
                "todo_write",
                "用一份结构化待办跟踪多步任务。merge 为 true 时按 id 或内容更新，否则整份替换。同一时间只能有一个 in_progress。",
                _object(
                    {
                        "todos": {
                            "type": "array",
                            "description": "待办列表。超过三步的任务用它跟踪，同一时间只能有一个 in_progress。",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": _prop("integer", "已有待办的编号。更新时填写。"),
                                    "content": _prop("string", "待办内容。"),
                                    "status": _prop("string", "pending、in_progress、done 或 cancelled。completed 会按 done 保存。", enum=["pending", "in_progress", "done", "cancelled"]),
                                    "activeForm": _prop("string", "进行中时显示的动作，例如“正在改设置”。"),
                                },
                                "required": ["content", "status"],
                            },
                        },
                        "merge": _prop("boolean", "为 true 时按 id 或内容更新已有项，这是默认值。为 false 时用这份列表替换全部待办。"),
                    },
                    ["todos"],
                ),
                _todo_write,
            ),
            Tool(
                "task_output",
                "读取后台命令的输出。没跑完时会说明仍在运行。",
                _object({"id": _prop("string", "后台任务编号，就是启动时返回的 # 后面那段。task_id 和 job_id 也会认。")}, ["id"]),
                _task_output,
                True,
            ),
            Tool(
                "task_stop",
                "停止一个后台命令。",
                _object({"id": _prop("string", "要停止的后台任务编号。task_id 和 job_id 也会认。")}, ["id"]),
                _task_stop,
            ),
            Tool(
                "list_schedules",
                "查看当前会话的定时任务。",
                _object({}, []),
                _list_schedules,
                True,
            ),
            Tool(
                "cancel_schedule",
                "停用一个定时任务。",
                _object({"id": _prop("integer", "要停用的定时任务编号。")}, ["id"]),
                _cancel_schedule,
            ),
            Tool(
                "list_mcp_resources",
                "列出 MCP 服务暴露的资源。",
                _object({}, []),
                _list_mcp_resources,
                True,
            ),
            Tool(
                "read_mcp_resource",
                "按 URI 读取某个 MCP 资源。",
                _object(
                    {
                        "server": _prop("string", "MCP 服务名。"),
                        "uri": _prop("string", "资源 URI。"),
                    },
                    ["server", "uri"],
                ),
                _read_mcp_resource,
                True,
            ),
            Tool(
                "enter_worktree",
                "只在用户明确说工作树或 worktree 时使用。修 bug、加功能、新建分支、切换分支都不要调用。在 git 仓库里新建隔离工作树并切换过去。",
                _object({"name": _prop("string", "工作树名称。不填则使用 task。")}, []),
                _enter_worktree,
            ),
            Tool(
                "exit_worktree",
                "只在用户明确要求离开工作树时调用。做完不要自己退出。回到原来的工作区并移除该工作树。",
                _object({}, []),
                _exit_worktree,
            ),
            Tool(
                "tool_search",
                "MCP 工具很多、没有直接出现在工具列表里时，用关键词找出要调用的 mcp__ 工具。",
                _object({"query": _prop("string", "用来匹配 MCP 工具名或说明的关键词。")}, ["query"]),
                _tool_search,
                True,
            ),
            Tool(
                "send_message",
                "给一个子代理的 id 送一条消息。对方在当前步骤或下次 resume 时会读到。",
                _object(
                    {
                        "to": _prop("string", "子代理 id，就是 spawn_agent 返回的那一个。"),
                        "text": _prop("string", "要送过去的消息。"),
                    },
                    ["to", "text"],
                ),
                _send_message,
                True,
            ),
            Tool(
                "lsp",
                "用语言服务查定义、引用、诊断、悬停说明、文件符号、工作区符号、实现或调用层次。action 可以是 definition、references、diagnostics、hover、document_symbol、workspace_symbol、implementation、prepare_call_hierarchy、incoming_calls、outgoing_calls。line 和 character 从 1 开始。workspace_symbol 用 query。",
                _object(
                    {
                        "path": _prop("string", "源文件路径，用工作区相对路径或绝对路径。"),
                        "action": _prop(
                            "string",
                            "要查询的动作。默认 definition。workspace_symbol 用 query，调用层次的第二步可以带上一步返回的 item。",
                            enum=["definition", "references", "diagnostics", "hover", "document_symbol", "workspace_symbol", "implementation", "prepare_call_hierarchy", "incoming_calls", "outgoing_calls"],
                        ),
                        "line": _prop("integer", "行号，从 1 开始。"),
                        "character": _prop("integer", "列号，从 1 开始。"),
                        "query": _prop("string", "workspace_symbol 的符号名。"),
                        "item": _prop("object", "prepare_call_hierarchy 返回的条目。incoming_calls 和 outgoing_calls 可以带着它。"),
                    },
                    ["path"],
                ),
                _lsp,
                True,
            ),
            Tool(
                "sleep",
                "等待最多 10 秒。只在必须等外部变化时使用。",
                _object({"seconds": _prop("integer", "等待秒数。默认 1，最多 10。只在必须等外部变化时使用。")}, []),
                _sleep,
                True,
            ),
        ]
    )
