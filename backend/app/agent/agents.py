"""内置和自定义子代理。自定义定义放在 .harness/agents 或 .claude/agents。"""

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

EXPLORE_SKIP = {"ask_user", "enter_plan_mode", "exit_plan_mode"}
PLAN_EXTRA = {"write_file", "edit_file", "todo_write", "task_write", "ask_user"}
MODES = {"auto", "ask", "plan", "edits", "dontAsk"}


@dataclass
class AgentSpec:
    name: str
    description: str
    prompt: str
    tools: list[str] | None = None
    disallowed: frozenset[str] = frozenset()
    permission_mode: str | None = None
    max_turns: int | None = None
    source: str = "built-in"


def normalize_kind(raw: str | None) -> str:
    text = (raw or "general").strip().lower().replace("_", "-")
    if text in {"general", "general-purpose"}:
        return "general"
    if text in {"explore", "search"}:
        return "explore"
    if text == "plan":
        return "plan"
    return text or "general"


def instructions(kind: str) -> str:
    if kind == "explore":
        return (
            "你是只读的代码搜索子代理。只能搜索和分析现有代码。\n"
            "禁止：新建、修改、删除、移动或复制文件，包括临时目录；"
            "禁止用重定向或 here-string 写文件；禁止任何会改变系统状态的命令。"
            "你没有编辑工具，尝试去改会失败。\n"
            "用 glob_files 按模式找文件，用 grep 搜内容，已知路径用 read_file。"
            "bash 只用于只读操作，例如 Get-ChildItem、git status、git log、git diff。"
            "不要用 bash 做 mkdir、Remove-Item、Copy-Item、Move-Item、git add、git commit、安装依赖，或任何会写文件的事。\n"
            "按调用方给的仔细程度搜索：quick、medium 或 very thorough。"
            "能并行的 grep 和 read_file 放在同一轮。尽快返回，最后用中文给出路径、行号和结论，不要写文件。"
        )
    if kind == "plan":
        return (
            "你是方案子代理。先把代码库看清，再设计实施步骤。\n"
            "除了把方案写入工作区的 .harness/plan.md，禁止新建、修改、删除、移动或复制其他文件，"
            "禁止用重定向或 here-string 写文件，禁止会改变系统状态的命令。不要再派子代理。\n"
            "过程：先读调用方给出的文件；用 glob_files、grep 和 read_file 找现有写法和相似功能，理清相关调用路径；"
            "bash 只用于只读操作。然后写出步骤、依赖顺序、取舍和可能的难点。\n"
            "方案写进 .harness/plan.md。回复末尾用中文列出 3 到 5 个实现时最关键的文件路径，并说明方案要点。"
        )
    return (
        "你是通用子代理。用现有工具把交给你的任务做完。不要镀金，也不要停在半成品。\n"
        "调用方看不到你的工具过程，只看你最后的中文报告，所以报告只要要点。\n"
        "不知道位置时先广搜，已知路径再用 read_file。分析时先宽后窄，第一次没找到就换一种搜法。"
        "多看几个位置和命名。不要新建完成目标所不必需的文件，优先改已有文件，不要主动写 Markdown 或 README。\n"
        "需要再查代码时只能再派 explore 或 plan。报告里写清改了哪些文件、命令是否成功，以及关键发现。"
    )


def agent_system_prompt(spec: AgentSpec, workspace: Path | None) -> str:
    """子代理提示：角色说明，再加上和主会话相同的环境与报告约定。"""
    import platform

    root = str(workspace) if workspace is not None else ""
    notes = (
        f"工作区：{root}\n"
        f"系统：{platform.platform()}\n"
        "终端是持续的 PowerShell，工作目录是这个工作区。同一会话里上一条命令设置的环境变量还在。\n"
        "最终报告里给出相关文件的绝对路径。只在原文本身是结论时贴代码，例如出错的那几行或调用方要的函数签名，不要复述只是读过的代码。\n"
        "不要用表情符号。工具调用前的句子用句号结束，不要用冒号。"
    )
    return spec.prompt.strip() + "\n\n" + notes


def _builtin(name: str) -> AgentSpec:
    descriptions = {
        "general": "复杂调查、找代码和多步任务。前几次搜索没把握时交给它",
        "explore": "按模式找文件、按关键词搜代码、回答代码库怎么工作。任务里写明 quick、medium 或 very thorough",
        "plan": "设计实现步骤、标出关键文件和取舍。方案写入 .harness/plan.md，不改业务代码",
    }
    kind = name if name in descriptions else "general"
    return AgentSpec(
        name=kind,
        description=descriptions[kind],
        prompt=instructions(kind),
        permission_mode="plan" if kind in {"explore", "plan"} else None,
        source="built-in",
    )


def _split_tools(value: str) -> list[str] | None:
    text = (value or "").strip()
    if not text:
        return []
    if text == "*":
        return None
    pieces = []
    for chunk in text.replace("\n", ",").split(","):
        name = chunk.strip().strip("[]\"'")
        if name and name != "-":
            pieces.append(name)
    if "*" in pieces:
        return None
    return pieces


def _read_agent(path: Path, source: str) -> AgentSpec | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta = {}
    body = raw.strip()
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip()] = value.strip()
            body = parts[2].strip()
    name = meta.get("name") or path.stem
    if not name or not body:
        return None
    mode = meta.get("permissionMode") or meta.get("permission_mode") or ""
    turns = meta.get("maxTurns") or meta.get("max_turns") or ""
    max_turns = int(turns) if str(turns).isdigit() else None
    return AgentSpec(
        name=name,
        description=meta.get("description") or body.splitlines()[0][:80],
        prompt=body,
        tools=_split_tools(meta["tools"]) if "tools" in meta else None,
        disallowed=frozenset(_split_tools(meta.get("disallowedTools") or meta.get("disallowed_tools") or "") or []),
        permission_mode=mode if mode in MODES else None,
        max_turns=max_turns,
        source=source,
    )


def _agent_dirs(workspace: Path | None) -> list[tuple[Path, str]]:
    dirs = [(Path.home() / ".harness" / "agents", "user")]
    if workspace is not None:
        dirs.append((workspace / ".claude" / "agents", "claude"))
        dirs.append((workspace / ".harness" / "agents", "project"))
    return dirs


def load_custom(workspace: Path | None) -> dict[str, AgentSpec]:
    found: dict[str, AgentSpec] = {}
    for folder, source in _agent_dirs(workspace):
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.md")):
            spec = _read_agent(path, source)
            if spec is not None:
                found[spec.name] = spec
    return found


def resolve_agent(raw: str | None, workspace: Path | None) -> AgentSpec | None:
    custom = load_custom(workspace)
    text = (raw or "general").strip()
    if text in custom:
        return custom[text]
    kind = normalize_kind(text)
    if kind in custom:
        return custom[kind]
    if kind in {"general", "explore", "plan"} and text.lower().replace("_", "-") in {
        "",
        "general",
        "general-purpose",
        "explore",
        "search",
        "plan",
    }:
        return _builtin(kind)
    if not text:
        return _builtin("general")
    return None


def catalog(workspace: Path | None = None) -> str:
    specs = {
        "general": _builtin("general"),
        "explore": _builtin("explore"),
        "plan": _builtin("plan"),
    }
    specs.update(load_custom(workspace))
    lines = []
    for spec in specs.values():
        suffix = "" if spec.source == "built-in" else f"（{spec.source}）"
        lines.append(f"- {spec.name}：{spec.description}{suffix}")
    return "\n".join(lines)


def _builtin_tools(kind: str, registry: list) -> list:
    if kind == "general":
        return list(registry)
    explore = [
        tool
        for tool in registry
        if (tool.read_only or tool.name == "bash") and tool.name not in EXPLORE_SKIP
    ]
    if kind == "explore":
        return explore
    names = {tool.name for tool in explore}
    extra = [tool for tool in registry if tool.name in PLAN_EXTRA and tool.name not in names]
    return explore + extra


def select(kind: str, registry: list, spec: AgentSpec | None = None) -> list:
    chosen = _builtin_tools(kind if kind in {"general", "explore", "plan"} else "general", registry)
    if spec is not None and spec.tools is not None:
        wanted = set(spec.tools)
        chosen = [tool for tool in registry if tool.name in wanted]
    blocked = set(spec.disallowed) if spec is not None else set()
    kept = []
    for tool in chosen:
        if tool.name in blocked:
            continue
        if tool.name == "spawn_agent" and kind not in {"general"}:
            continue
        kept.append(tool)
    return kept


def _run_path(workspace: Path, run_id: str) -> Path:
    return workspace / ".harness" / "agent-runs" / f"{run_id}.json"


def save_run(workspace: Path, run_id: str, agent: str, messages: list[dict]) -> None:
    path = _run_path(workspace, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"id": run_id, "agent": agent, "messages": messages}, ensure_ascii=False),
        encoding="utf-8",
    )


def load_run(workspace: Path, run_id: str) -> dict | None:
    path = _run_path(workspace, run_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        return None
    return data


def new_run_id() -> str:
    return uuid.uuid4().hex[:8]
