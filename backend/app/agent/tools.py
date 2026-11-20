import asyncio
import base64
import fnmatch
import ipaddress
import json
import os
import platform
import re
import shutil
import struct
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select

from ..config import UPLOAD_DIR
from ..models import Attachment, McpServer, Schedule, Skill, Task
from . import mcp_client
from .edits import apply_once, change_snippet, remember_read, require_fresh_read, resolve_edit, unchanged_read
from .safety import blocked_command, sensitive_reason
from .shellguard import command_is_readonly
from .state import JOBS, tag_job

SKIP_DIRS = {".git", "node_modules", "__pycache__", "dist", ".venv", "uploads", ".harness"}
MAX_TOOL_CHARS = 20000
RESULT_LIMIT = 50_000
PREVIEW_CHARS = 2_000
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600
MAX_READ_BYTES = 256 * 1024
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
TYPE_EXTS = {
    "py": {".py"},
    "js": {".js", ".jsx", ".mjs", ".cjs"},
    "ts": {".ts", ".tsx"},
    "vue": {".vue"},
    "go": {".go"},
    "rust": {".rs"},
    "java": {".java"},
    "md": {".md", ".mdx"},
    "json": {".json"},
    "html": {".html", ".htm"},
    "css": {".css"},
    "yml": {".yml", ".yaml"},
    "sh": {".sh", ".ps1", ".bat"},
}


@dataclass
class ToolResult:
    ok: bool
    text: str
    attachments: list[int] = field(default_factory=list)
    diff: str = ""
    image: dict | None = None


@dataclass
class ToolContext:
    db: object
    workspace: Path
    conversation_id: int | None
    permission_mode: str
    broker: object
    emit: object
    provider_name: str
    model: str
    depth: int = 0
    hub: object = None
    turn_token: str = ""
    conversation_key: int | None = None
    origin_workspace: Path | None = None
    history_batch: str = ""
    agent_run_id: str = ""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: object
    read_only: bool = False


def _decode_output(data: bytes) -> str:
    if not data:
        return ""
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    if data.count(b"\x00") > max(1, len(data) // 4):
        return data.decode("utf-16-le", errors="replace")
    return data.decode("utf-8", errors="replace")


def clip(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…已截断，原始长度 {len(text)} 字符"


def _command_timeout(raw) -> int:
    if raw in (None, ""):
        return DEFAULT_TIMEOUT
    return max(1, min(int(raw), MAX_TIMEOUT))


def bound_output(text: str, workspace: Path | None, label: str = "tool") -> str:
    """超过 5 万字时把全文落到工作区，只把预览交回模型。"""
    body = text or ""
    if len(body) <= RESULT_LIMIT:
        return body
    folder = (workspace or Path.cwd()) / ".harness" / "tool-results"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{label}-{uuid.uuid4().hex[:8]}.txt"
    path.write_text(body, encoding="utf-8")
    preview = body[:PREVIEW_CHARS]
    return (
        f"输出过长（{len(body)} 字符）。完整内容已保存到 {path}\n"
        f"预览（前 {PREVIEW_CHARS} 字符）：\n{preview}\n…\n"
        "需要后文时用 read_file 的 offset 和 limit 读取这个文件，不要原样重跑。"
    )


def substitute_arguments(content: str, args: str | None, argument_names: str = "") -> str:
    """替换技能正文里的 $ARGUMENTS、$ARGUMENTS[0] 和 $0。没有占位符时把参数附在末尾。"""
    if args is None:
        return content
    parsed = [part for part in args.split() if part]
    names = [name for name in str(argument_names or "").split() if name and not name.isdigit()]
    updated = content
    for index, name in enumerate(names):
        updated = re.sub(rf"\${re.escape(name)}(?![\w\[])", parsed[index] if index < len(parsed) else "", updated)
    updated = re.sub(
        r"\$ARGUMENTS\[(\d+)\]",
        lambda match: parsed[int(match.group(1))] if int(match.group(1)) < len(parsed) else "",
        updated,
    )
    updated = re.sub(r"\$(\d+)(?!\w)", lambda match: parsed[int(match.group(1))] if int(match.group(1)) < len(parsed) else "", updated)
    updated = updated.replace("$ARGUMENTS", args)
    if updated == content and args:
        updated = content + f"\n\nARGUMENTS: {args}"
    return updated


def resolve_path(workspace: Path, raw: str) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        path = workspace / path
    return path


def guard_path(path: Path) -> str | None:
    return sensitive_reason(path)


def openai_schema(tool: Tool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _object(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


def _prop(kind: str, description: str, **extra) -> dict:
    spec = {"type": kind, "description": description}
    spec.update(extra)
    return spec


def _powershell_argv(command: str) -> tuple[list[str], dict]:
    from .shell_session import child_env

    wrapped = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + command
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", wrapped], child_env()


def _command_text(stdout: bytes, stderr: bytes) -> str:
    out = _decode_output(stdout)
    err = _decode_output(stderr)
    text = ""
    if out.strip():
        text += out
    if err.strip():
        text += ("\n" if text else "") + err
    return text.strip() or "（无输出）"


async def _bash(args: dict, ctx: ToolContext) -> ToolResult:
    command = str(args.get("command") or "").strip()
    if not command:
        return ToolResult(False, "缺少 command")
    reason = blocked_command(command)
    if reason:
        return ToolResult(False, reason)
    if args.get("run_in_background"):
        return await _bash_background(command, ctx)
    timeout = _command_timeout(args.get("timeout"))
    from .shell_session import run_shell, shell_key

    try:
        code, text = await run_shell(ctx.workspace, command, timeout, shell_key(ctx.workspace, ctx.conversation_id, ctx.agent_run_id))
    except TimeoutError:
        return ToolResult(False, f"命令超时（{timeout} 秒）")
    except RuntimeError as exc:
        return ToolResult(False, str(exc))
    return ToolResult(code == 0, bound_output(f"退出码 {code}\n{text}", ctx.workspace, "bash"))


async def _bash_background(command: str, ctx: ToolContext) -> ToolResult:
    from .shell_session import current_cwd, shell_key

    argv, env = _powershell_argv(command)
    cwd = current_cwd(ctx.workspace, shell_key(ctx.workspace, ctx.conversation_id, ctx.agent_run_id))
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    job_id = uuid.uuid4().hex[:8]
    JOBS[job_id] = {"proc": proc, "command": command, "done": False, "text": "", "code": None, "kind": "command"}
    tag_job(JOBS[job_id], ctx)

    async def watch() -> None:
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=MAX_TIMEOUT)
            text = _command_text(stdout, stderr)
            code = proc.returncode
        except TimeoutError:
            proc.kill()
            text = f"后台命令超过 {MAX_TIMEOUT} 秒，已停止"
            code = -1
        except Exception as exc:
            text = str(exc)
            code = -1
        job = JOBS.get(job_id)
        if job is not None:
            job["text"] = text
            job["code"] = code
            job["done"] = True

    asyncio.create_task(watch())
    return ToolResult(True, f"已在后台启动，结束后会通知，不用轮询。任务编号 #{job_id}")


def _image_size(data: bytes) -> tuple[int, int] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if data[:2] != b"\xff\xd8":
        return None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in {0xC0, 0xC1, 0xC2}:
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return int(width), int(height)
        if index + 4 > len(data):
            break
        size = struct.unpack(">H", data[index + 2 : index + 4])[0]
        index += 2 + size
    return None


def _page_numbers(spec: str, total: int) -> list[int]:
    chosen: list[int] = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            chosen.extend(range(start, end + 1))
        else:
            chosen.append(int(part))
    seen: list[int] = []
    for number in chosen:
        if 1 <= number <= total and number not in seen:
            seen.append(number)
        if len(seen) >= 20:
            break
    return seen


async def _read_file(args: dict, ctx: ToolContext) -> ToolResult:
    raw = str(args.get("path") or args.get("file_path") or "")
    path = resolve_path(ctx.workspace, raw)
    reason = guard_path(path)
    if reason:
        return ToolResult(False, reason)
    if not path.exists():
        return ToolResult(False, f"文件不存在: {path}")
    if path.is_dir():
        return ToolResult(False, "这是目录，请用 list_dir")
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        data = path.read_bytes()
        size = _image_size(data)
        shape = f"，{size[0]}x{size[1]}" if size else ""
        media = IMAGE_TYPES.get(suffix)
        if media and len(data) <= 1_500_000:
            return ToolResult(
                True,
                f"图片文件 {path.name}{shape}，{len(data)} 字节。",
                image={"media_type": media, "data": base64.b64encode(data).decode("ascii")},
            )
        return ToolResult(True, f"图片文件 {path.name}{shape}，{len(data)} 字节。文件过大或不是可直接查看的格式，没有放进上下文。")
    if suffix == ".pdf":
        return ToolResult(True, bound_output(_read_pdf(path, args.get("pages")), ctx.workspace, "pdf"))
    if suffix == ".docx":
        return ToolResult(True, bound_output(_read_docx(path), ctx.workspace, "docx"))
    if suffix == ".ipynb":
        raw_text = path.read_text(encoding="utf-8")
        remember_read(path, raw_text, False)
        return ToolResult(True, bound_output(_read_ipynb(path), ctx.workspace, "notebook"))
    if path.stat().st_size > MAX_READ_BYTES and not args.get("offset") and not args.get("limit"):
        return ToolResult(False, f"文件有 {path.stat().st_size} 字节，超过 256KB。请用 offset 和 limit 分段读取。")
    offset = max(int(args.get("offset") or 1), 1)
    limit = int(args.get("limit") or 2000)
    cached = unchanged_read(path, offset, limit)
    if cached:
        return ToolResult(True, cached)
    data = path.read_bytes()
    if b"\x00" in data[:1024]:
        return ToolResult(True, f"二进制文件 {path.name}，大小 {len(data)} 字节。")
    text = data.decode("utf-8", errors="replace")
    if text == "":
        remember_read(path, "", False, reusable=True, offset=offset, limit=limit)
        return ToolResult(True, "文件存在，但内容是空的。")
    lines = text.splitlines()
    start = offset - 1
    chunk = lines[start : start + max(limit, 1)]
    partial = start != 0 or start + len(chunk) < len(lines)
    remember_read(
        path,
        None if partial else text,
        partial,
        reusable=not partial,
        offset=offset,
        limit=limit,
    )
    numbered = [f"{index + start + 1}|{line}" for index, line in enumerate(chunk)]
    header = (
        f"{path} 共 {len(lines)} 行，显示 {start + 1}-{start + len(chunk)}。"
        "行号前缀是 行号| ，替换时不要把前缀写进原文。"
    )
    if partial:
        header += " 这是片段，改文件前需要不带 limit 再读一遍。"
    from .memory import freshness_prefix

    note = freshness_prefix(path)
    body = (note if note else "") + header + "\n" + "\n".join(numbered)
    return ToolResult(True, bound_output(body, ctx.workspace, "read"))


def _read_pdf(path: Path, pages: str | None = None) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "没有安装 PDF 解析组件。"
    reader = PdfReader(str(path))
    total = len(reader.pages)
    if pages:
        try:
            indexes = _page_numbers(str(pages), total)
        except ValueError:
            return "pages 需要类似 1-5 或 2,4 的写法"
        if not indexes:
            return "没有这个页码范围"
    elif total > 20:
        return f"PDF 共 {total} 页。超过 20 页时必须用 pages 指定范围，例如 1-5。每次最多 20 页。"
    else:
        indexes = list(range(1, total + 1))
    chunks = []
    for number in indexes:
        chunks.append(f"--- 第 {number} 页 ---\n{reader.pages[number - 1].extract_text() or ''}")
    body = "\n".join(chunks) or "PDF 没有可提取的文本。"
    if not pages and total:
        body = f"共 {total} 页\n" + body
    return body


def _read_docx(path: Path) -> str:
    from docx import Document

    document = Document(str(path))
    parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    return "\n".join(parts) or "文档没有正文。"


def _read_ipynb(path: Path) -> str:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    chunks = []
    for index, cell in enumerate(notebook.get("cells") or []):
        source = cell.get("source") or ""
        if isinstance(source, list):
            source = "".join(source)
        chunks.append(f"--- cell {index} id={cell.get('id') or index} ({cell.get('cell_type')}) ---\n{source}")
        for output in (cell.get("outputs") or [])[:3]:
            text = output.get("text") or ""
            if isinstance(text, list):
                text = "".join(text)
            if text:
                chunks.append("输出: " + str(text)[:500])
    return "\n".join(chunks) or "笔记本是空的。"


def _backup(path: Path, workspace: Path) -> None:
    try:
        folder = workspace / ".harness" / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{path.name}.{datetime.now().strftime('%H%M%S%f')}.bak"
        shutil.copy2(path, target)
    except OSError:
        return


async def _write_file(args: dict, ctx: ToolContext) -> ToolResult:
    path = resolve_path(ctx.workspace, str(args.get("path") or args.get("file_path") or ""))
    reason = guard_path(path)
    if reason:
        return ToolResult(False, reason)
    content = args.get("content")
    if content is None:
        return ToolResult(False, "缺少 content")
    if path.exists() and path.is_file():
        stale = require_fresh_read(path)
        if stale:
            return ToolResult(False, stale + " 已有文件请优先用 edit_file。")
        _backup(path, ctx.workspace)
    elif path.exists():
        return ToolResult(False, "目标是目录，不能当文件写")
    from .diffutil import unified
    from .history import remember_change

    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    remember_change(ctx, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = str(content)
    path.write_text(text, encoding="utf-8")
    remember_read(path, text, False)
    return ToolResult(
        True,
        f"已写入 {path}，{path.stat().st_size} 字节",
        diff=unified(path.name, before, text),
    )


async def _edit_file(args: dict, ctx: ToolContext) -> ToolResult:
    path = resolve_path(ctx.workspace, str(args.get("path") or args.get("file_path") or ""))
    reason = guard_path(path)
    if reason:
        return ToolResult(False, reason)
    if path.suffix.lower() == ".ipynb":
        return ToolResult(False, "这是 Jupyter 笔记本，请改用 notebook_edit")
    old = args.get("old", args.get("old_string"))
    new = args.get("new", args.get("new_string"))
    if old is None or new is None:
        return ToolResult(False, "需要 old 和 new")
    replace_all = bool(args.get("replace_all"))
    from .diffutil import unified
    from .history import remember_change

    if not path.exists():
        if str(old) != "":
            return ToolResult(False, f"文件不存在: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        remember_change(ctx, path)
        path.write_text(str(new), encoding="utf-8")
        remember_read(path, str(new), False)
        return ToolResult(True, f"已新建 {path}", diff=unified(path.name, "", str(new)))
    if not path.is_file():
        return ToolResult(False, "目标不是文件")
    stale = require_fresh_read(path)
    if stale:
        return ToolResult(False, stale)
    text = path.read_text(encoding="utf-8")
    if str(old) == "":
        if text != "":
            return ToolResult(False, "文件已有内容，不能用空原文覆盖")
        remember_change(ctx, path)
        path.write_text(str(new), encoding="utf-8")
        remember_read(path, str(new), False)
        return ToolResult(True, f"已写入空文件 {path}", diff=unified(path.name, text, str(new)))
    located = resolve_edit(text, str(old), str(new), path)
    if located is None:
        return ToolResult(False, "没有找到要替换的原文")
    actual, replacement = located
    count = text.count(actual)
    if count > 1 and not replace_all:
        return ToolResult(False, f"原文出现了 {count} 次。要全部替换请把 replace_all 设为 true")
    updated = apply_once(text, actual, replacement, replace_all)
    if updated == text:
        return ToolResult(False, "替换后文件没有变化")
    _backup(path, ctx.workspace)
    remember_change(ctx, path)
    path.write_text(updated, encoding="utf-8")
    remember_read(path, updated, False)
    line = text.find(actual)
    start = text[:line].count("\n") + 1 if line >= 0 else 1
    times = count if replace_all else 1
    return ToolResult(
        True,
        f"已替换 {times} 处: {path}\n{change_snippet(updated, replacement, start)}",
        diff=unified(path.name, text, updated),
    )


def _allow_dir(parent: Path, name: str) -> bool:
    """工作区搜索可以进入 .harness/memory，其余隐藏目录仍然跳过。"""
    if parent.name == ".harness":
        return name == "memory"
    if name == ".harness":
        return True
    return name not in SKIP_DIRS and not name.startswith(".")


def _skipped_path(path: Path) -> bool:
    from .memory import in_topic_memory

    if in_topic_memory(path):
        return False
    return any(part in SKIP_DIRS or (part.startswith(".") and part not in {".", ".."}) for part in path.parts)


def _walk(root: Path, visit_limit: int = 20000):
    if root.is_file():
        yield root
        return
    visits = 0
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [name for name in dirnames if _allow_dir(current, name)]
        if current.name == ".harness":
            continue
        for name in filenames:
            visits += 1
            if visits > visit_limit:
                return
            yield current / name


async def _glob_files(args: dict, ctx: ToolContext) -> ToolResult:
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        return ToolResult(False, "缺少 pattern")
    base = resolve_path(ctx.workspace, str(args.get("path") or "."))
    reason = guard_path(base)
    if reason:
        return ToolResult(False, reason)
    if not base.exists():
        return ToolResult(False, f"路径不存在: {base}")
    matches = []
    if base.is_file():
        matches = [base] if base.match(pattern) else []
    else:
        try:
            matches = [item for item in base.glob(pattern) if item.is_file()]
        except NotImplementedError:
            return ToolResult(False, "这个通配符在当前系统上不能用")
    files = [path for path in matches if not _skipped_path(path)]
    files.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    lines = [str(path) for path in files[:200]]
    if not lines:
        return ToolResult(True, "没有匹配的文件")
    extra = "" if len(files) <= 200 else f"\n只显示前 200 个，共 {len(files)} 个"
    return ToolResult(True, bound_output("\n".join(lines) + extra, ctx.workspace, "glob"))


def _glob_ok(path: Path, root: Path, pattern: str) -> bool:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.name
    if "/" in pattern or "**" in pattern:
        return fnmatch.fnmatch(relative, pattern)
    return fnmatch.fnmatch(path.name, pattern)


def _type_ok(path: Path, file_type: str) -> bool:
    if not file_type:
        return True
    exts = TYPE_EXTS.get(file_type.lower())
    if exts:
        return path.suffix.lower() in exts
    return path.suffix.lower() == f".{file_type.lower()}"


def _context_bounds(args: dict) -> tuple[int, int]:
    both = args.get("-C", args.get("context", args.get("C")))
    if both not in (None, ""):
        value = max(0, min(int(both), 20))
        return value, value
    before = max(0, min(int(args.get("-B") or args.get("B") or 0), 20))
    after = max(0, min(int(args.get("-A") or args.get("A") or 0), 20))
    return before, after


async def _grep_rg(args: dict, ctx: ToolContext, root: Path, pattern: str) -> ToolResult | None:
    rg = shutil.which("rg")
    if not rg:
        return None
    mode = str(args.get("output_mode") or "content")
    argv = [rg, "--color", "never", "--max-columns", "240", "--max-columns-preview"]
    if mode == "files_with_matches":
        argv.append("--files-with-matches")
    elif mode == "count":
        argv.append("--count")
    else:
        argv.append("--line-number")
        before, after = _context_bounds(args)
        if before:
            argv.extend(["-B", str(before)])
        if after:
            argv.extend(["-A", str(after)])
    if args.get("ignore_case") or args.get("-i"):
        argv.append("-i")
    if args.get("multiline"):
        argv.extend(["-U", "--multiline-dotall"])
    glob_pat = str(args.get("glob") or "")
    if glob_pat:
        argv.extend(["--glob", glob_pat])
    file_type = str(args.get("type") or "")
    if file_type:
        argv.extend(["--type", file_type])
    head = args.get("head_limit")
    head = 50 if head in (None, "") else max(0, int(head))
    if head and mode == "content":
        argv.extend(["--max-count", str(head)])
    argv.extend(["-e", pattern, str(root)])
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(ctx.workspace),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20)
    except TimeoutError:
        proc.kill()
        return ToolResult(False, "搜索超时")
    if proc.returncode not in {0, 1}:
        detail = _decode_output(stderr).strip()
        if "unrecognized" in detail.lower() or "error" in detail.lower():
            return None
        return ToolResult(False, detail or "ripgrep 失败")
    text = _decode_output(stdout).strip()
    if not text:
        return ToolResult(True, "没有匹配内容")
    offset = max(0, int(args.get("offset") or 0))
    rows = text.splitlines()
    window = rows[offset : offset + head] if head else rows[offset:]
    extra = f"\n已达到 {head} 条上限" if head and len(rows) > offset + head else ""
    return ToolResult(True, bound_output("\n".join(window) + extra, ctx.workspace, "grep"))


async def _grep(args: dict, ctx: ToolContext) -> ToolResult:
    pattern = str(args.get("pattern") or "")
    if not pattern:
        return ToolResult(False, "缺少 pattern")
    root = resolve_path(ctx.workspace, str(args.get("path") or "."))
    reason = guard_path(root)
    if reason:
        return ToolResult(False, reason)
    if not root.exists():
        return ToolResult(False, f"路径不存在: {root}")
    searched = await _grep_rg(args, ctx, root, pattern)
    if searched is not None:
        return searched
    flags = re.IGNORECASE if args.get("ignore_case") or args.get("-i") else 0
    if args.get("multiline"):
        flags |= re.DOTALL
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        return ToolResult(False, f"正则无效: {exc}")
    root = resolve_path(ctx.workspace, str(args.get("path") or "."))
    reason = guard_path(root)
    if reason:
        return ToolResult(False, reason)
    if not root.exists():
        return ToolResult(False, f"路径不存在: {root}")
    mode = str(args.get("output_mode") or "content")
    glob_pat = str(args.get("glob") or "")
    file_type = str(args.get("type") or "")
    head = args.get("head_limit")
    head = 50 if head in (None, "") else max(0, int(head))
    offset = max(0, int(args.get("offset") or 0))
    before, after = _context_bounds(args)
    binary = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".docx", ".zip", ".exe"}
    rows: list[str] = []
    for file in _walk(root):
        if file.suffix.lower() in binary:
            continue
        if glob_pat and not _glob_ok(file, root, glob_pat):
            continue
        if not _type_ok(file, file_type):
            continue
        try:
            if file.stat().st_size > 1_000_000:
                continue
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "\x00" in text[:1024]:
            continue
        lines = text.splitlines()
        numbers: list[int] = []
        if args.get("multiline"):
            for match in regex.finditer(text):
                numbers.append(text[: match.start()].count("\n") + 1)
        else:
            for number, line in enumerate(lines, start=1):
                if regex.search(line):
                    numbers.append(number)
        if not numbers:
            continue
        if mode == "files_with_matches":
            rows.append(str(file))
        elif mode == "count":
            rows.append(f"{file}:{len(numbers)}")
        else:
            shown: set[int] = set()
            for number in numbers:
                for nearby in range(max(1, number - before), min(len(lines), number + after) + 1):
                    if nearby in shown:
                        continue
                    shown.add(nearby)
                    mark = ":" if nearby in numbers else "-"
                    rows.append(f"{file}{mark}{nearby}{mark}{lines[nearby - 1][:240]}")
        if head and len(rows) >= head + offset:
            break
    if not rows:
        return ToolResult(True, "没有匹配内容")
    window = rows[offset : offset + head] if head else rows[offset:]
    extra = ""
    if head and len(rows) > offset + head:
        extra = f"\n已达到 {head} 条上限"
    return ToolResult(True, bound_output("\n".join(window) + extra, ctx.workspace, "grep"))


async def _list_dir(args: dict, ctx: ToolContext) -> ToolResult:
    path = resolve_path(ctx.workspace, str(args.get("path") or "."))
    reason = guard_path(path)
    if reason:
        return ToolResult(False, reason)
    if not path.exists():
        return ToolResult(False, f"路径不存在: {path}")
    if path.is_file():
        return ToolResult(True, f"文件 {path} {path.stat().st_size} 字节")
    rows = []
    for child in sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))[:200]:
        if child.name in SKIP_DIRS:
            continue
        kind = "dir" if child.is_dir() else "file"
        size = "" if child.is_dir() else f" {child.stat().st_size}B"
        rows.append(f"{kind}\t{child.name}{size}")
    return ToolResult(True, "\n".join(rows) or "空目录")


async def search_web(query: str, limit: int = 5) -> list[dict]:
    results: list[dict] = []
    headers = {"User-Agent": "Mozilla/5.0 DuckHarness/0.1"}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
        try:
            response = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            for node in soup.select(".result"):
                link = node.select_one(".result__a")
                if not link:
                    continue
                snippet = node.select_one(".result__snippet")
                results.append(
                    {
                        "title": link.get_text(" ", strip=True),
                        "url": link.get("href") or "",
                        "snippet": snippet.get_text(" ", strip=True) if snippet else "",
                    }
                )
                if len(results) >= limit:
                    return results
        except Exception:
            results = []
        if len(results) < limit:
            wiki = await client.get(
                "https://zh.wikipedia.org/w/api.php",
                params={
                    "action": "opensearch",
                    "search": query,
                    "limit": str(limit),
                    "namespace": "0",
                    "format": "json",
                },
            )
            if wiki.status_code == 200:
                payload = wiki.json()
                titles = payload[1] if len(payload) > 1 else []
                descriptions = payload[2] if len(payload) > 2 else []
                urls = payload[3] if len(payload) > 3 else []
                for title, desc, url in zip(titles, descriptions, urls):
                    results.append({"title": title, "url": url, "snippet": desc})
    return results[:limit]


def _blocked_url(url: str) -> str | None:
    if not re.match(r"^https?://", url or "", re.IGNORECASE):
        return "只允许 http 或 https 链接"
    host = (urlparse(url).hostname or "").strip("[]").lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return "拒绝访问本机地址"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
        return "拒绝访问内网地址"
    return None


async def fetch_url(url: str, limit: int = 1_000_000) -> str:
    blocked = _blocked_url(url)
    if blocked:
        return blocked
    headers = {"User-Agent": "Mozilla/5.0 DuckHarness/0.1"}
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        raw = response.content[:1_000_000]
        if "html" in content_type or raw[:200].lstrip().lower().startswith(b"<!doctype html") or b"<html" in raw[:400].lower():
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text("\n", strip=True)
        else:
            text = raw.decode("utf-8", errors="replace")
        if len(text) > limit:
            return text[:limit]
        return text


async def _web_search(args: dict, ctx: ToolContext) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult(False, "缺少 query")
    rows = await search_web(query, int(args.get("limit") or 5))
    if not rows:
        return ToolResult(True, "没有搜到结果")
    lines = [f"{item['title']}\n{item['url']}\n{item['snippet']}" for item in rows]
    return ToolResult(True, bound_output("\n\n".join(lines), ctx.workspace, "search"))


async def _extract_page(ctx: ToolContext, url: str, text: str, prompt: str) -> str:
    if ctx.hub is None or not ctx.model:
        return text
    from .providers import collect_text

    provider = ctx.hub.resolve(ctx.provider_name, ctx.db)
    source = text[:30_000]
    question = (
        f"下面是网页 {url} 的正文。只根据正文提取要求的信息，正文里没有的不要编造。\n"
        f"要提取的内容：{prompt}\n\n{source}"
    )
    try:
        extracted = await collect_text(provider, ctx.model, question, max_tokens=800)
    except Exception as exc:
        return f"按说明提取失败：{exc}\n\n{text[:4000]}"
    return extracted or text[:4000]


async def _web_fetch(args: dict, ctx: ToolContext) -> ToolResult:
    url = str(args.get("url") or "").strip()
    if not url:
        return ToolResult(False, "缺少 url")
    prompt = str(args.get("prompt") or "").strip()
    try:
        text = await fetch_url(url)
    except Exception as exc:
        return ToolResult(False, f"抓取失败: {exc}")
    if prompt:
        text = await _extract_page(ctx, url, text, prompt)
    return ToolResult(True, bound_output(text, ctx.workspace, "fetch"))


async def _remember(args: dict, ctx: ToolContext) -> ToolResult:
    from .memory import save_memory

    content = str(args.get("content") or "").strip()
    if not content:
        return ToolResult(False, "缺少 content")
    name = str(args.get("name") or args.get("title") or content[:40]).strip()
    try:
        saved = save_memory(
            ctx.workspace,
            name,
            content,
            str(args.get("type") or "user"),
            str(args.get("description") or ""),
        )
    except ValueError as exc:
        return ToolResult(False, str(exc))
    return ToolResult(
        True,
        f"已记住 {saved['name']}（{saved['type_label']}）。索引已更新。同名再次调用会覆盖这一条，不会另记一条。",
    )


async def _list_memories(args: dict, ctx: ToolContext) -> ToolResult:
    from .memory import sync_index

    text = sync_index(ctx.workspace).strip()
    if not text:
        return ToolResult(True, "还没有记忆")
    return ToolResult(True, text)


async def _read_memory(args: dict, ctx: ToolContext) -> ToolResult:
    from .memory import read_memory

    name = str(args.get("name") or args.get("title") or "").strip()
    if not name:
        return ToolResult(False, "缺少 name")
    try:
        return ToolResult(True, read_memory(ctx.workspace, name))
    except ValueError as exc:
        return ToolResult(False, str(exc))


async def _forget_memory(args: dict, ctx: ToolContext) -> ToolResult:
    from .memory import forget_memory

    name = str(args.get("name") or args.get("title") or "").strip()
    if not name:
        return ToolResult(False, "缺少 name")
    try:
        removed = forget_memory(ctx.workspace, name)
    except ValueError as exc:
        return ToolResult(False, str(exc))
    return ToolResult(True, f"已忘掉 {removed}")


async def _task_write(args: dict, ctx: ToolContext) -> ToolResult:
    title = str(args.get("title") or "").strip()
    task_id = args.get("id")
    status = str(args.get("status") or "pending")
    if status not in {"pending", "in_progress", "done", "cancelled"}:
        return ToolResult(False, "status 只能是 pending、in_progress、done、cancelled")
    if task_id:
        row = ctx.db.get(Task, int(task_id))
        if row is None or (ctx.conversation_id is not None and row.conversation_id != ctx.conversation_id):
            return ToolResult(False, "任务不存在")
        if title:
            row.title = title
        if args.get("detail") is not None:
            row.detail = str(args.get("detail"))
        row.status = status
        row.updated_at = datetime.now()
    else:
        if not title:
            return ToolResult(False, "新建任务需要 title")
        row = Task(
            conversation_id=ctx.conversation_id,
            title=title,
            detail=str(args.get("detail") or ""),
            status=status,
        )
        ctx.db.add(row)
    ctx.db.commit()
    return ToolResult(True, f"任务 #{row.id} [{row.status}] {row.title}")


async def _list_tasks(args: dict, ctx: ToolContext) -> ToolResult:
    statement = select(Task).order_by(Task.id.desc()).limit(40)
    if ctx.conversation_id:
        statement = (
            select(Task)
            .where(Task.conversation_id == ctx.conversation_id)
            .order_by(Task.id.asc())
        )
    rows = ctx.db.scalars(statement).all()
    if not rows:
        return ToolResult(True, "没有任务")
    return ToolResult(True, "\n".join(f"#{row.id} [{row.status}] {row.title} {row.detail}".rstrip() for row in rows))


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()
    meta = {}
    for line in parts[1].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, parts[2].strip()


def _absorb_skill(found: dict, path: Path, source: str) -> None:
    try:
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return
    if path.name.lower() == "skill.md":
        name = meta.get("name") or path.parent.name
    else:
        name = meta.get("name") or path.stem
    found[name] = {
        "name": name,
        "description": meta.get("description", ""),
        "body": body,
        "arguments": meta.get("arguments") or "",
        "source": source,
        "directory": str(path.parent),
    }


def load_skills(db, workspace: Path | None = None) -> list[dict]:
    from ..config import BACKEND_DIR

    found: dict[str, dict] = {}
    skill_dir = BACKEND_DIR / "skills"
    if skill_dir.exists():
        for path in sorted(skill_dir.glob("*.md")):
            _absorb_skill(found, path, "file")
    if workspace is not None:
        for root in (
            workspace / "skills",
            workspace / ".claude" / "skills",
            workspace / ".harness" / "skills",
        ):
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.md")):
                _absorb_skill(found, path, "workspace")
    for row in db.scalars(select(Skill).where(Skill.enabled.is_(True))).all():
        found[row.name] = {
            "name": row.name,
            "description": row.description,
            "body": row.body,
            "arguments": "",
            "source": "db",
        }
    return list(found.values())


_SKILL_BLOCK = re.compile(r"```!\s*\n?([\s\S]*?)\n?```")
_SKILL_INLINE = re.compile(r"(?:(?<=^)|(?<=\s))!`([^`]+)`", re.M)


async def _run_skill_command(command: str, ctx: ToolContext) -> str:
    command = command.strip()
    if not command:
        return ""
    reason = blocked_command(command)
    if reason:
        raise RuntimeError(reason)
    from ..services.runtime import canonical_mode

    mode = canonical_mode(ctx.permission_mode)
    if mode == "plan" and not command_is_readonly(command):
        raise RuntimeError("计划模式只允许只读命令")
    if mode in {"ask", "edits", "dontAsk"} and not command_is_readonly(command):
        raise RuntimeError("这条命令需要确认，请改用 bash 执行")
    argv, env = _powershell_argv(command)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(ctx.workspace),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        proc.kill()
        raise RuntimeError("技能里的命令超过 30 秒")
    text = _command_text(stdout, stderr)
    if proc.returncode not in (0, None):
        raise RuntimeError(text)
    return text


async def expand_skill_commands(content: str, ctx: ToolContext, skill_dir: str | None) -> str:
    """先替换技能目录，再执行正文里的 !`命令` 和 ```! 代码块，把输出填回去。"""
    if skill_dir:
        content = content.replace("${CLAUDE_SKILL_DIR}", skill_dir.replace("\\", "/"))
    if "```!" not in content and "!`" not in content:
        return content
    matches = list(_SKILL_BLOCK.finditer(content)) + list(_SKILL_INLINE.finditer(content))
    updated = content
    for match in matches:
        output = await _run_skill_command(match.group(1), ctx)
        updated = updated.replace(match.group(0), output, 1)
    return updated


async def _use_skill(args: dict, ctx: ToolContext) -> ToolResult:
    name = str(args.get("name") or args.get("skill") or "").strip()
    skills = {item["name"]: item for item in load_skills(ctx.db, ctx.workspace)}
    skill = skills.get(name)
    if skill is None:
        return ToolResult(False, "没有这个技能。可用: " + ", ".join(skills))
    raw_args = args.get("args")
    if isinstance(raw_args, list):
        raw_args = " ".join(str(item) for item in raw_args)
    elif raw_args is not None:
        raw_args = str(raw_args)
    body = substitute_arguments(skill["body"], raw_args, skill.get("arguments") or "")
    try:
        body = await expand_skill_commands(body, ctx, skill.get("directory"))
    except RuntimeError as exc:
        return ToolResult(False, f"技能 {name} 里的命令没有执行：{exc}")
    return ToolResult(True, f"技能 {name}\n{skill['description']}\n\n{body}")


async def _generate_docx(args: dict, ctx: ToolContext) -> ToolResult:
    from docx import Document

    title = str(args.get("title") or "未命名文档").strip()
    content = str(args.get("content") or "").strip()
    if not content:
        return ToolResult(False, "缺少 content")
    if ctx.conversation_id is None:
        return ToolResult(False, "没有会话，不能挂附件")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    folder = UPLOAD_DIR / str(ctx.conversation_id)
    folder.mkdir(parents=True, exist_ok=True)
    filename = str(args.get("filename") or f"{title}.docx")
    if not filename.lower().endswith(".docx"):
        filename += ".docx"
    filename = Path(filename).name
    target = folder / filename
    document = Document()
    document.add_heading(title, level=1)
    for block in content.split("\n"):
        document.add_paragraph(block)
    document.save(target)
    attachment = Attachment(
        conversation_id=ctx.conversation_id,
        filename=filename,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        size=target.stat().st_size,
        path=str(target),
    )
    ctx.db.add(attachment)
    ctx.db.commit()
    return ToolResult(True, f"已生成 Word 文档 {filename}（{attachment.size} 字节）", [attachment.id])


async def _git_summary(args: dict, ctx: ToolContext) -> ToolResult:
    git = shutil.which("git")
    if not git:
        return ToolResult(False, "没有找到 git")
    chunks = []
    for argv in (
        [git, "rev-parse", "--is-inside-work-tree"],
        [git, "status", "--short"],
        [git, "log", "-5", "--oneline"],
        [git, "diff", "--stat"],
    ):
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(ctx.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        text = (stdout or stderr).decode("utf-8", errors="replace").strip()
        chunks.append(f"$ {' '.join(argv[1:])}\n{text or '（无输出）'}")
        if argv[1] == "rev-parse" and proc.returncode != 0:
            return ToolResult(True, "当前工作区不是 git 仓库。\n" + chunks[0])
    return ToolResult(True, clip("\n\n".join(chunks)))


async def _schedule_task(args: dict, ctx: ToolContext) -> ToolResult:
    prompt = str(args.get("prompt") or "").strip()
    if not prompt:
        return ToolResult(False, "缺少 prompt")
    every = int(args.get("every_seconds") or 0)
    run_at = args.get("run_at")
    if every <= 0 and not run_at:
        return ToolResult(False, "需要 every_seconds 或 run_at")
    if every:
        next_run = datetime.now() + timedelta(seconds=max(every, 30))
    else:
        try:
            next_run = datetime.fromisoformat(str(run_at))
        except ValueError:
            return ToolResult(False, "run_at 需要 ISO 时间，例如 2026-11-17T10:00:00")
    row = Schedule(
        conversation_id=ctx.conversation_id,
        prompt=prompt,
        every_seconds=max(every, 0),
        next_run=next_run,
        enabled=True,
    )
    ctx.db.add(row)
    ctx.db.commit()
    return ToolResult(True, f"已安排 #{row.id}，下次运行 {next_run.isoformat(sep=' ', timespec='seconds')}")


async def _system_info(args: dict, ctx: ToolContext) -> ToolResult:
    usage = shutil.disk_usage(ctx.workspace if ctx.workspace.exists() else Path.home())
    payload = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "workspace": str(ctx.workspace),
        "free_gb": round(usage.free / (1024 ** 3), 2),
    }
    return ToolResult(True, json.dumps(payload, ensure_ascii=False))


async def _open_path(args: dict, ctx: ToolContext) -> ToolResult:
    path = resolve_path(ctx.workspace, str(args.get("path") or ""))
    reason = guard_path(path)
    if reason:
        return ToolResult(False, reason)
    if not path.exists():
        return ToolResult(False, f"路径不存在: {path}")
    os.startfile(path)  # type: ignore[attr-defined]
    return ToolResult(True, f"已用系统默认程序打开 {path}")


async def _servers(db):
    return db.scalars(select(McpServer).where(McpServer.enabled.is_(True)).order_by(McpServer.id)).all()


async def _list_mcp_tools(args: dict, ctx: ToolContext) -> ToolResult:
    rows = await _servers(ctx.db)
    if not rows:
        return ToolResult(True, "还没有启用的 MCP 服务")
    chunks = []
    for row in rows:
        try:
            tools = await mcp_client.operate(
                row.command,
                row.args or [],
                row.env or {},
                lambda client: client.list_tools(),
            )
            names = ", ".join(item.get("name", "") for item in tools) or "（无工具）"
            chunks.append(f"{row.name}: {names}")
        except Exception as exc:
            chunks.append(f"{row.name}: 连接失败 {exc}")
    return ToolResult(True, "\n".join(chunks))


async def _call_mcp_tool(args: dict, ctx: ToolContext) -> ToolResult:
    server_name = str(args.get("server") or "").strip()
    tool_name = str(args.get("tool") or "").strip()
    if not server_name or not tool_name:
        return ToolResult(False, "需要 server 和 tool")
    row = next((item for item in await _servers(ctx.db) if item.name == server_name), None)
    if row is None:
        return ToolResult(False, "没有这个 MCP 服务")
    arguments = args.get("arguments") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return ToolResult(False, "arguments 不是合法 JSON")
    try:
        text = await mcp_client.operate(
            row.command,
            row.args or [],
            row.env or {},
            lambda client, tool_name=tool_name, arguments=arguments: client.call_tool(tool_name, arguments),
        )
        return ToolResult(True, clip(text))
    except Exception as exc:
        return ToolResult(False, f"MCP 调用失败: {exc}")


async def _spawn_agent(args: dict, ctx: ToolContext) -> ToolResult:
    if ctx.depth >= 2:
        return ToolResult(False, "子代理最多再派一层只读子代理")
    from .agents import agent_system_prompt, load_run, new_run_id, resolve_agent, save_run, select
    from .loop import run_loop
    from .permissions import agent_denied

    root = ctx.origin_workspace or ctx.workspace
    resume = str(args.get("resume") or "").strip()
    task = str(args.get("task") or args.get("prompt") or "").strip()
    saved = load_run(root, resume) if resume else None
    if resume and saved is None:
        return ToolResult(False, f"没有这个子代理记录: {resume}")
    if not task and not resume:
        return ToolResult(False, "缺少 task")
    raw_name = str(args.get("agent") or args.get("subagent_type") or (saved or {}).get("agent") or "general")
    if ctx.depth >= 1 and raw_name not in {"explore", "plan"}:
        return ToolResult(False, "子代理只能再派只读的 explore 或 plan")
    spec = resolve_agent(raw_name, root)
    if spec is None:
        return ToolResult(False, "没有这个子代理。可用：\n" + catalog_text(root))
    denied = agent_denied(root, spec.name)
    if denied:
        return ToolResult(False, denied)
    mode = spec.permission_mode or ("plan" if spec.name in {"explore", "plan"} else ctx.permission_mode)
    child = ToolContext(
        db=ctx.db,
        workspace=ctx.workspace,
        conversation_id=ctx.conversation_id,
        permission_mode=mode,
        broker=ctx.broker,
        emit=ctx.emit,
        provider_name=ctx.provider_name,
        model=ctx.model,
        depth=ctx.depth + 1,
        hub=ctx.hub,
        turn_token=ctx.turn_token,
        conversation_key=ctx.conversation_key,
        origin_workspace=root,
        history_batch=ctx.history_batch,
    )
    run_id = resume if saved else new_run_id()
    child.agent_run_id = run_id
    if saved:
        messages = list(saved["messages"])
        messages.append({"role": "user", "content": task or "继续上次未完成的部分。"})
    else:
        plan_path = plan_display(child) if spec.name == "plan" or (mode == "plan" and spec.name != "explore") else None
        messages = [
            {"role": "system", "content": agent_system_prompt(spec, root, plan_path)},
            {"role": "user", "content": task},
        ]
    allowed = [tool.name for tool in select(spec.name, REGISTRY, spec)]
    if spec.tools:
        allowed.extend(name for name in spec.tools if str(name).startswith("mcp__") and name not in allowed)
    default_steps = spec.max_turns or (40 if spec.name == "explore" else 200)
    steps = int(args.get("max_steps") or default_steps)

    async def run_child() -> str:
        session = None
        try:
            if ctx.db is not None:
                from ..db import open_session

                session = open_session()
                child.db = session
            provider = ctx.hub.resolve(ctx.provider_name, child.db)
            return await run_loop(
                messages,
                provider,
                child,
                max_steps=steps,
                allowed=allowed,
            )
        finally:
            if session is not None:
                session.close()

    if args.get("run_in_background"):
        job_id = uuid.uuid4().hex[:8]
        JOBS[job_id] = {"kind": "agent", "done": False, "text": "", "code": None, "command": task, "notified": False}
        tag_job(JOBS[job_id], ctx)

        async def work() -> None:
            try:
                final_text = await run_child()
                save_run(root, run_id, spec.name, messages)
                JOBS[job_id]["text"] = final_text or "子代理没有给出结果"
                JOBS[job_id]["code"] = 0
            except Exception as exc:
                JOBS[job_id]["text"] = str(exc)
                JOBS[job_id]["code"] = 1
            finally:
                JOBS[job_id]["done"] = True

        asyncio.create_task(work())
        return ToolResult(True, f"子代理 {run_id}（{spec.name}）已在后台运行，结束后会通知，不用轮询。任务编号 #{job_id}")
    final = await run_child()
    save_run(root, run_id, spec.name, messages)
    body = clip(final or "子代理没有给出结果")
    return ToolResult(True, f"子代理 {run_id}（{spec.name}）\n{body}")


def catalog_text(workspace: Path) -> str:
    from .agents import catalog

    return catalog(workspace)


REGISTRY: list[Tool] = [
    Tool(
        "bash",
        "在本机 PowerShell 执行命令。工作目录是当前工作区。前台默认 120 秒，最长 600 秒。run_in_background 为 true 时在单独的 PowerShell 里后台运行，结束后会通知，不要轮询。计划模式只允许明确的只读命令。",
        _object(
            {
                "command": _prop("string", "要执行的 PowerShell 命令。工作目录是当前工作区。"),
                "timeout": _prop("integer", "前台超时，单位是秒。默认 120，最大 600。"),
                "run_in_background": _prop("boolean", "为 true 时放到后台。结束后会通知，不要轮询，也不要在命令末尾加 &。"),
                "description": _prop("string", "用一句主动语态说明这条命令做什么，例如“查看 git 状态”。"),
            },
            ["command"],
        ),
        _bash,
    ),
    Tool(
        "read_file",
        "读取文本、PDF、Word、Jupyter 或图片。图片会把像素交给模型。默认最多 2000 行。PDF 超过 20 页时用 pages，例如 1-5。改文件前必须先完整读取。",
        _object(
            {
                "path": _prop("string", "要读取的文件。用工作区相对路径或绝对路径，不要传目录。"),
                "offset": _prop("integer", "从第几行开始读，从 1 计。文件不长时不要填。"),
                "limit": _prop("integer", "最多读多少行。默认 2000。"),
                "pages": _prop("string", "PDF 页码，例如 1-5、3 或 2,4。超过 20 页时必须填写，每次最多 20 页。"),
            },
            ["path"],
        ),
        _read_file,
        True,
    ),
    Tool(
        "write_file",
        "新建文本文件。覆盖已有文件前必须先 read_file，已有文件优先用 edit_file。",
        _object(
            {
                "path": _prop("string", "要写入的文件，用工作区相对路径或绝对路径。"),
                "content": _prop("string", "文件的完整内容。"),
            },
            ["path", "content"],
        ),
        _write_file,
    ),
    Tool(
        "edit_file",
        "替换文件中的一段原文。必须先完整 read_file。原文不唯一时要设 replace_all。不要把行号前缀写进 old/new。笔记本请用 notebook_edit。",
        _object(
            {
                "path": _prop("string", "要修改的文件，用工作区相对路径或绝对路径。"),
                "old": _prop("string", "要替换的原文，必须和文件里的文本一致，不要带行号前缀。"),
                "new": _prop("string", "替换后的文本，必须和 old 不同。"),
                "replace_all": _prop("boolean", "为 true 时替换每一处。原文不唯一时必须设为 true。默认 false。"),
            },
            ["path", "old", "new"],
        ),
        _edit_file,
    ),
    Tool(
        "glob_files",
        "按通配符找文件，例如 **/*.py。结果按修改时间从新到旧。",
        _object(
            {
                "pattern": _prop("string", "通配符，例如 **/*.py 或 src/**/*.vue。"),
                "path": _prop("string", "从哪个目录开始找。不填则使用工作区根目录。"),
            },
            ["pattern"],
        ),
        _glob_files,
        True,
    ),
    Tool(
        "grep",
        "在文件内容里搜索正则。output_mode 可以是 content、files_with_matches、count。可用 glob、type、head_limit、context、multiline。",
        _object(
            {
                "pattern": _prop("string", "ripgrep 正则。要匹配字面量大括号时需要转义。"),
                "path": _prop("string", "文件或目录。不填则搜索整个工作区。"),
                "glob": _prop("string", "按文件名过滤，例如 *.py 或 *.{ts,vue}。"),
                "type": _prop("string", "按语言过滤，例如 py、js、ts、go。比 glob 更适合常见类型。"),
                "output_mode": _prop("string", "content 返回匹配行，这是默认值。files_with_matches 只返回路径。count 返回每个文件的匹配次数。", enum=["content", "files_with_matches", "count"]),
                "head_limit": _prop("integer", "最多返回多少条。默认 50。0 表示不截断。"),
                "offset": _prop("integer", "跳过前多少条再返回。默认 0。"),
                "context": _prop("integer", "匹配行前后各带多少行，最多 20。只在 content 模式生效。"),
                "-A": _prop("integer", "匹配行后面再带多少行，最多 20。只在 content 模式生效。"),
                "-B": _prop("integer", "匹配行前面再带多少行，最多 20。只在 content 模式生效。"),
                "-C": _prop("integer", "匹配行前后各带多少行，最多 20。只在 content 模式生效。"),
                "multiline": _prop("boolean", "为 true 时点号可以跨行。默认 false。"),
                "ignore_case": _prop("boolean", "为 true 时忽略大小写。"),
                "-i": _prop("boolean", "为 true 时忽略大小写，和 ignore_case 相同。"),
            },
            ["pattern"],
        ),
        _grep,
        True,
    ),
    Tool("list_dir", "列出目录的一层内容。", _object({"path": _prop("string", "要列出的目录。不填则列出工作区根目录。")}, []), _list_dir, True),
    Tool(
        "web_search",
        "联网搜索，返回标题、链接和摘要。",
        _object(
            {
                "query": _prop("string", "搜索词。查近期资料时带上当前年份。"),
                "limit": _prop("integer", "返回条数。默认 5。"),
            },
            ["query"],
        ),
        _web_search,
        True,
    ),
    Tool(
        "web_fetch",
        "抓取一个 http 或 https 页面。prompt 写要提取的内容，只返回这部分，不要整页塞进上下文。",
        _object(
            {
                "url": _prop("string", "完整的 http 或 https 地址。"),
                "prompt": _prop("string", "要从页面里提取什么。填写后只返回提取结果。"),
            },
            ["url"],
        ),
        _web_fetch,
        True,
    ),
    Tool(
        "remember",
        "把以后的会话还要用的事实写入 .harness/memory。type 只能是 user、feedback、project、reference。同名会更新原文件和 MEMORY.md 索引，不会另记一条。不要用它记代码结构、git 历史或这一轮的步骤。",
        _object(
            {
                "content": _prop("string", "要记住的事实正文。"),
                "name": _prop("string", "记忆名称。同名会更新原来的条目。不填时用 title 或正文开头。"),
                "title": _prop("string", "显示名。没有 name 时也可以用它作为名称。"),
                "description": _prop("string", "写进 MEMORY.md 索引的一行说明。"),
                "type": _prop("string", "记忆类型。默认 user。", enum=["user", "feedback", "project", "reference"]),
            },
            ["content"],
        ),
        _remember,
    ),
    Tool("list_memories", "查看长期记忆的索引。每条只有一行说明，正文用 read_memory。", _object({}, []), _list_memories, True),
    Tool("read_memory", "按名称读取一条长期记忆的正文。超过一天的记忆会标明它是当时的观察。", _object({"name": _prop("string", "记忆名称。")}, ["name"]), _read_memory, True),
    Tool("forget_memory", "按名称删除一条长期记忆，并把它从 MEMORY.md 索引里去掉。", _object({"name": _prop("string", "要删除的记忆名称。")}, ["name"]), _forget_memory),
    Tool(
        "task_write",
        "创建或更新任务。status 可用 pending、in_progress、done、cancelled。",
        _object(
            {
                "title": _prop("string", "任务标题。新建时需要。"),
                "detail": _prop("string", "补充说明。"),
                "status": _prop("string", "pending、in_progress、done 或 cancelled。completed 会按 done 保存。默认 pending。", enum=["pending", "in_progress", "done", "cancelled"]),
                "id": _prop("integer", "要更新的任务编号。不填则新建。"),
            },
            [],
        ),
        _task_write,
    ),
    Tool("list_tasks", "查看当前会话的任务。", _object({}, []), _list_tasks, True),
    Tool(
        "use_skill",
        "读取一个技能的操作说明并按它执行。args 会替换正文里的 $ARGUMENTS、$0 或附在说明末尾。说明里的 !`命令` 和 ```! 代码块会先在 PowerShell 里执行，输出填回说明。",
        _object(
            {
                "name": _prop("string", "技能名称，必须是当前列出的技能。"),
                "args": _prop("string", "传给技能的参数，会替换 $ARGUMENTS 和 $0。"),
            },
            ["name"],
        ),
        _use_skill,
        True,
    ),
    Tool(
        "generate_docx",
        "生成一份 Word 文档并挂到当前会话。",
        _object(
            {
                "title": _prop("string", "文档标题。"),
                "content": _prop("string", "文档正文。"),
                "filename": _prop("string", "文件名。不填则用标题。"),
            },
            ["title", "content"],
        ),
        _generate_docx,
    ),
    Tool(
        "spawn_agent",
        "派一个子代理处理复杂、多步的任务，或用 resume 续跑。只在任务对得上该子代理的说明时使用，用来并行独立调查，或避免大量原始结果留在当前上下文。不需要时不要派。"
        "agent 可以是 general、explore、plan，或 .harness/agents 与 .claude/agents 里的名字。不填则用 general。explore 只读，plan 只写计划，general 可以改代码，并且还能再派一层 explore 或 plan。"
        "已知文件路径、两三个文件里的内容、或一次就能定位的类名，直接用 read_file、glob_files 或 grep，不要派子代理。一次直接查找不够，或明显要超过 3 次查询时，再用 explore。"
        "task 要写清目的、已经看过的情况、是改代码还是只做调查，以及具体改什么。子代理从零开始，看不到这段对话。它的结果用户看不到，一般可以采信，你要用中文转述要点。"
        "下一步要等结果才能继续时用前台，不要设 run_in_background。彼此独立的 explore 或 plan 放在同一轮会一起执行，同时最多 10 个。general 会等正在进行的改动结束。可以同时做、又不急着要结果的调查才设 run_in_background。后台结束后会通知，不用轮询。返回里的 id 用于 resume，也可以用 send_message 把话送到这个 id。",
        _object(
            {
                "task": _prop("string", "交给子代理的任务。它看不到这段对话，要写清目的、已经看过的情况和具体改什么。"),
                "agent": _prop("string", "general、explore、plan，或已安装的子代理名。不填则用 general。"),
                "subagent_type": _prop("string", "和 agent 相同。两个都填时以 agent 为准。"),
                "resume": _prop("string", "上次返回的子代理 id，用来继续那个子代理。"),
                "max_steps": _prop("integer", "这个子代理最多走多少步。不填则用该子代理的默认上限。"),
                "run_in_background": _prop("boolean", "为 true 时立刻返回。结束后会通知，不要轮询。"),
            },
            [],
        ),
        _spawn_agent,
    ),
    Tool("git_summary", "查看当前工作区的 git 状态、最近提交和 diff 统计。", _object({}, []), _git_summary, True),
    Tool(
        "schedule_task",
        "安排以后自动执行的提示。every_seconds 至少按 30 秒计，或提供 run_at。",
        _object(
            {
                "prompt": _prop("string", "到点后要执行的提示。"),
                "every_seconds": _prop("integer", "重复间隔，单位是秒，至少按 30 秒计。"),
                "run_at": _prop("string", "第一次运行的时间，例如 2026-11-20 09:00。"),
            },
            ["prompt"],
        ),
        _schedule_task,
    ),
    Tool("list_mcp_tools", "列出已配置 MCP 服务上的原始工具名。若工具列表里已经有 mcp__ 开头的工具，直接调用那些。", _object({}, []), _list_mcp_tools, True),
    Tool(
        "call_mcp_tool",
        "按服务名和原始工具名调用 MCP。若工具列表里已经有对应的 mcp__ 工具，直接调用那个。",
        _object(
            {
                "server": _prop("string", "MCP 服务名。"),
                "tool": _prop("string", "该服务上的原始工具名。"),
                "arguments": _prop("object", "传给该工具的参数。没有参数时传空对象。"),
            },
            ["server", "tool"],
        ),
        _call_mcp_tool,
    ),
    Tool("system_info", "查看系统、Python、工作区和磁盘剩余空间。", _object({}, []), _system_info, True),
    Tool("open_path", "用系统默认程序打开工作区里的文件或目录。", _object({"path": _prop("string", "要打开的文件或目录，用工作区相对路径或绝对路径。")}, ["path"]), _open_path),
]

BY_NAME = {tool.name: tool for tool in REGISTRY}


def _name_denied(name: str, blocked: set[str]) -> bool:
    if name in blocked:
        return True
    return any(item.startswith("mcp__") and name.startswith(item + "__") for item in blocked)


def schemas(allowed: list[str] | None = None, workspace: Path | None = None) -> list[dict]:
    from .mcp_bridge import GENERIC, visible_bridged

    tools = list(REGISTRY)
    if allowed is not None:
        tools = [tool for tool in tools if tool.name in allowed]
    bridged = visible_bridged(allowed)
    if bridged and len(bridged) > 16:
        tools = [tool for tool in tools if tool.name != "list_mcp_tools"]
        names = {tool.name for tool in tools}
        if "tool_search" not in names and (allowed is None or "tool_search" in allowed):
            search = next((tool for tool in REGISTRY if tool.name == "tool_search"), None)
            if search is not None:
                tools.append(search)
    elif bridged:
        tools = [tool for tool in tools if tool.name not in GENERIC]
        tools.extend(bridged)
    if workspace is not None:
        from .permissions import denied_tool_names

        blocked = denied_tool_names(workspace)
        tools = [tool for tool in tools if not _name_denied(tool.name, blocked)]
    return [openai_schema(tool) for tool in tools]


class ApprovalBroker:
    def __init__(self):
        self.pending: dict[str, dict] = {}

    async def request(self, payload: dict) -> bool:
        import uuid

        approval_id = uuid.uuid4().hex
        event = asyncio.Event()
        self.pending[approval_id] = {"event": event, "approved": False, **payload}
        emit = payload.get("emit")
        if emit:
            await emit(
                {
                    "type": "approval_required",
                    "approval_id": approval_id,
                    "name": payload.get("name"),
                    "arguments": payload.get("arguments") or {},
                    "preview": payload.get("preview") or "",
                }
            )
        try:
            await asyncio.wait_for(event.wait(), timeout=float(payload.get("timeout") or 120))
        except TimeoutError:
            self.pending.pop(approval_id, None)
            return False
        item = self.pending.pop(approval_id, {})
        return bool(item.get("approved"))

    def resolve(self, approval_id: str, approved: bool, answers: dict | None = None) -> bool:
        item = self.pending.get(approval_id)
        if not item:
            return False
        item["approved"] = approved
        item["answers"] = answers
        item["event"].set()
        return True

    async def ask(self, payload: dict) -> dict | None:
        approval_id = uuid.uuid4().hex
        event = asyncio.Event()
        self.pending[approval_id] = {"event": event, "approved": False, "answers": None, **payload}
        emit = payload.get("emit")
        if emit:
            await emit(
                {
                    "type": "question_required",
                    "approval_id": approval_id,
                    "questions": payload.get("questions") or [],
                }
            )
        try:
            await asyncio.wait_for(event.wait(), timeout=float(payload.get("timeout") or 180))
        except TimeoutError:
            self.pending.pop(approval_id, None)
            return None
        item = self.pending.pop(approval_id, {})
        if not item.get("approved"):
            return None
        return item.get("answers") or {}


broker = ApprovalBroker()

QUIET_ASK = {"todo_write", "task_write", "enter_plan_mode", "ask_user", "exit_plan_mode"}
PLAN_EXTRA = {"exit_plan_mode", "enter_plan_mode", "todo_write", "task_write", "ask_user", "remember", "forget_memory"}
EDITS_ASK = {"bash", "call_mcp_tool", "open_path", "schedule_task", "cancel_schedule", "enter_worktree", "exit_worktree"}


def _plan_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value).strip("-")
    return (cleaned or "agent")[:40]


def plan_filename(conversation_id: int | None, agent_run_id: str = "") -> str:
    """主会话一份，子代理在同一会话号后加自己的编号，避免互相覆盖。"""
    try:
        convo = str(int(conversation_id)) if conversation_id is not None else "0"
    except (TypeError, ValueError):
        convo = "0"
    if agent_run_id:
        return f"{convo}-agent-{_plan_token(agent_run_id)}.md"
    return f"{convo}.md"


def plan_file(ctx: ToolContext) -> Path:
    root = ctx.origin_workspace or ctx.workspace
    return root / ".harness" / "plans" / plan_filename(ctx.conversation_id, ctx.agent_run_id)


def plan_display(ctx: ToolContext) -> str:
    path = plan_file(ctx)
    try:
        return path.resolve().relative_to(ctx.workspace.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def is_plan_file(ctx: ToolContext, raw: str) -> bool:
    if not str(raw or "").strip():
        return False
    path = resolve_path(ctx.workspace, raw)
    plan = plan_file(ctx)
    try:
        return path.resolve() == plan.resolve()
    except OSError:
        return False


def _plan_allows(name: str, tool: Tool, arguments: dict, ctx: ToolContext) -> bool:
    if name == "bash":
        return command_is_readonly(str(arguments.get("command") or ""))
    if name in {"write_file", "edit_file"} and is_plan_file(
        ctx, str(arguments.get("path") or arguments.get("file_path") or "")
    ):
        return True
    if name in PLAN_EXTRA:
        return True
    return bool(tool.read_only)


_PATH_TOOLS = {"read_file", "write_file", "edit_file", "glob_files", "grep", "list_dir", "open_path", "lsp"}


def normalize_arguments(name: str, arguments: dict) -> dict:
    """校验前把 clawd 的字段名补到这里的字段上。已填写的字段保持不变，空字符串仍保留。"""
    copied = dict(arguments or {})
    pairs: list[tuple[str, str, bool]] = []
    if name in _PATH_TOOLS:
        pairs.extend([("file_path", "path", False), ("filePath", "path", False)])
    if name == "edit_file":
        pairs.extend([("old_string", "old", True), ("new_string", "new", True)])
    elif name == "use_skill":
        pairs.append(("skill", "name", False))
    elif name in {"task_output", "task_stop"}:
        pairs.extend([("task_id", "id", False), ("job_id", "id", False), ("shell_id", "id", False)])
    elif name == "notebook_edit":
        pairs.extend([("file_path", "notebook_path", False), ("filePath", "notebook_path", False), ("path", "notebook_path", False)])
    elif name == "send_message":
        pairs.append(("message", "text", False))
    elif name == "spawn_agent":
        pairs.append(("prompt", "task", False))
    for source, target, keep_blank in pairs:
        incoming = copied.get(source)
        if incoming is None:
            continue
        current = copied.get(target)
        blank = isinstance(current, str) and not current.strip()
        if current is None or (blank and not keep_blank):
            copied[target] = incoming
    if name in {"task_output", "task_stop"} and copied.get("id") is not None and not isinstance(copied["id"], str):
        copied["id"] = str(copied["id"]).lstrip("#")
    return copied


def argument_issues(name: str, schema: dict, arguments: dict) -> str:
    """把缺参数和类型不对写成模型能接着改的说明。未声明的字段不拒绝。"""
    properties = (schema or {}).get("properties") or {}
    required = (schema or {}).get("required") or []
    issues = []
    for key in required:
        if key not in arguments or arguments.get(key) is None:
            issues.append(f"缺少必填参数 `{key}`")
    type_of = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    for key, value in arguments.items():
        expected = (properties.get(key) or {}).get("type")
        if expected not in type_of or value is None:
            continue
        if expected == "integer" and isinstance(value, bool):
            issues.append(f"参数 `{key}` 应为 integer，实际是 boolean")
            continue
        if expected == "number" and isinstance(value, bool):
            issues.append(f"参数 `{key}` 应为 number，实际是 boolean")
            continue
        if not isinstance(value, type_of[expected]):
            issues.append(f"参数 `{key}` 应为 {expected}，实际是 {type(value).__name__}")
    if not issues:
        return ""
    head = f"{name} 的参数有问题：" if len(issues) == 1 else f"{name} 的参数有这些问题："
    return head + "\n" + "\n".join(issues)


def spill_text(ctx: ToolContext, text: str) -> str:
    if len(text) <= 12000:
        return text
    folder = ctx.workspace / ".harness" / "tool-results"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{uuid.uuid4().hex[:10]}.txt"
    target.write_text(text, encoding="utf-8")
    return text[:4000] + f"\n…结果较长，完整内容在 {target}"


async def execute(name: str, arguments: dict, ctx: ToolContext) -> ToolResult:
    from .mcp_bridge import find_tool

    tool = BY_NAME.get(name) or find_tool(name)
    if tool is None:
        return ToolResult(False, f"没有这个工具: {name}")
    arguments = normalize_arguments(name, arguments)
    root = ctx.origin_workspace or ctx.workspace
    from .diffutil import attach_diff
    from .hooks import after_tool, before_tool
    from .permissions import judge

    issues = argument_issues(name, tool.parameters, arguments)
    if issues:
        return ToolResult(False, issues)
    decision = judge(root, name, arguments)
    if decision.action == "deny":
        return ToolResult(False, f"权限规则拒绝了 {name}（{decision.rule}）")
    from ..services.runtime import canonical_mode

    mode = canonical_mode(ctx.permission_mode)
    if mode == "plan" and not _plan_allows(name, tool, arguments, ctx):
        return ToolResult(False, f"计划模式只允许查看，这次操作被拦住了。方案只能写入 {plan_display(ctx)}")
    hook = await before_tool(root, name, arguments)
    if hook.block:
        return ToolResult(False, hook.block)
    if hook.updated:
        arguments = normalize_arguments(name, hook.updated)
    mcp_write = name.startswith("mcp__") and not tool.read_only
    needs_ask = (mode in {"ask", "dontAsk"} and not tool.read_only and name not in QUIET_ASK) or (
        mode == "edits" and (name in EDITS_ASK or mcp_write)
    )
    if decision.action == "ask":
        needs_ask = True
    if decision.action == "allow":
        needs_ask = False
    if hook.ask:
        needs_ask = True
    if hook.allow and decision.action != "ask":
        needs_ask = False
    if needs_ask and mode == "dontAsk":
        return ToolResult(False, "当前是不询问模式，这次操作需要确认，已被拒绝")
    if needs_ask:
        allowed = await ctx.broker.request(
            {"name": name, "arguments": arguments, "emit": ctx.emit, "timeout": 120}
        )
        if not allowed:
            return ToolResult(False, "用户拒绝或没有及时确认这次操作")
    try:
        result = await tool.fn(arguments, ctx)
    except Exception as exc:
        ctx.db.rollback()
        return ToolResult(False, f"工具执行失败: {exc}")
    note = await after_tool(root, name, arguments, result.ok, result.text or "")
    if note:
        result.text = ((result.text or "") + "\n" + note).strip()
    if result.diff:
        result.text = attach_diff(result.text, result.diff)
    if result.ok and result.text:
        result.text = spill_text(ctx, result.text)
    return result


from .extra_tools import register

register(REGISTRY)
BY_NAME = {tool.name: tool for tool in REGISTRY}
