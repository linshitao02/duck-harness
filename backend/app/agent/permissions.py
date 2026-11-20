"""按工具名的允许、拒绝和询问。规则写在工作区或用户目录的 settings.json。"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

BEHAVIORS = ("deny", "ask", "allow")


@dataclass
class Decision:
    action: str
    rule: str = ""


def rule_paths(workspace: Path) -> list[Path]:
    home = Path.home() / ".harness" / "settings.json"
    return [
        home,
        workspace / ".harness" / "settings.json",
        workspace / ".claude" / "settings.json",
        workspace / ".harness" / "settings.local.json",
        workspace / ".claude" / "settings.local.json",
    ]


def parse_rule(text: str) -> tuple[str, str | None] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if "(" not in raw or not raw.endswith(")"):
        return raw, None
    name, _, rest = raw.partition("(")
    content = rest[:-1].strip()
    if content in {"", "*"}:
        return name.strip(), None
    return name.strip(), content


def _read_rules(path: Path) -> dict[str, list[tuple[str, str | None, str]]]:
    found = {key: [] for key in BEHAVIORS}
    if not path.is_file():
        return found
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return found
    permissions = data.get("permissions") if isinstance(data, dict) else None
    if not isinstance(permissions, dict):
        return found
    for behavior in BEHAVIORS:
        values = permissions.get(behavior) or []
        if not isinstance(values, list):
            continue
        for item in values:
            parsed = parse_rule(str(item))
            if parsed is None:
                continue
            found[behavior].append((parsed[0], parsed[1], str(item).strip()))
    return found


def load_rules(workspace: Path) -> dict[str, list[tuple[str, str | None, str]]]:
    merged = {key: [] for key in BEHAVIORS}
    for path in rule_paths(workspace):
        chunk = _read_rules(path)
        for behavior in BEHAVIORS:
            merged[behavior].extend(chunk[behavior])
    return merged


def content_matches(pattern: str, value: str) -> bool:
    if pattern.endswith(":*"):
        return value.lower().startswith(pattern[:-2].lower())
    body = re.escape(pattern).replace(r"\*", ".*")
    return re.search(f"^{body}$", value, re.IGNORECASE | re.DOTALL) is not None


def _subjects(name: str, arguments: dict, workspace: Path) -> list[str]:
    arguments = arguments or {}
    if name == "bash":
        return [str(arguments.get("command") or "")]
    if name == "spawn_agent":
        kind = arguments.get("agent") or arguments.get("subagent_type") or "general"
        return [str(kind)]
    raw = ""
    for key in ("path", "file_path", "notebook_path", "url", "query", "server"):
        if arguments.get(key):
            raw = str(arguments[key])
            break
    subjects = [raw] if raw else []
    if raw:
        try:
            relative = Path(raw)
            if not relative.is_absolute():
                relative = workspace / relative
            subjects.append(relative.resolve().relative_to(workspace.resolve()).as_posix())
        except (OSError, ValueError):
            subjects.append(raw.replace("\\", "/"))
    return subjects or [""]


def _subject_match(content: str, name: str, arguments: dict, workspace: Path) -> bool:
    return any(content_matches(content, subject) for subject in _subjects(name, arguments, workspace))


def _covers(rule_name: str, tool_name: str) -> bool:
    if rule_name in {tool_name, "*"}:
        return True
    return rule_name.startswith("mcp__") and tool_name.startswith(rule_name + "__")


def judge(workspace: Path, name: str, arguments: dict) -> Decision:
    rules = load_rules(workspace)
    for behavior in BEHAVIORS:
        for tool_name, content, label in rules[behavior]:
            if not _covers(tool_name, name):
                continue
            if content is None or _subject_match(content, name, arguments, workspace):
                return Decision(behavior, label)
    return Decision("default")


def denied_tool_names(workspace: Path) -> set[str]:
    blocked = set()
    for tool_name, content, _label in load_rules(workspace)["deny"]:
        if content is None and tool_name != "*":
            blocked.add(tool_name)
    return blocked


def agent_denied(workspace: Path, kind: str) -> str:
    decision = judge(workspace, "spawn_agent", {"agent": kind})
    if decision.action == "deny":
        return f"权限规则拒绝了子代理 {kind}（{decision.rule}）"
    return ""


def describe_rules(workspace: Path) -> str:
    rules = load_rules(workspace)
    lines = []
    labels = {"deny": "拒绝", "ask": "询问", "allow": "允许"}
    for behavior in BEHAVIORS:
        items = [label for _tool, _content, label in rules[behavior]]
        lines.append(f"{labels[behavior]}：" + ("、".join(items) if items else "（无）"))
    lines.append("写法：/permissions deny bash(Remove-Item*)，也可以 allow 或 ask。/permissions clear 只清本地规则。")
    return "\n".join(lines)


def _local_path(workspace: Path) -> Path:
    return workspace / ".harness" / "settings.local.json"


def _update_local(workspace: Path, mutate) -> None:
    path = _local_path(workspace)
    data = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        permissions = {}
    mutate(permissions)
    data["permissions"] = permissions
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def add_local_rule(workspace: Path, behavior: str, rule: str) -> str:
    if behavior not in BEHAVIORS:
        return "规则类型只能是 deny、ask 或 allow。"
    parsed = parse_rule(rule)
    if parsed is None:
        return "规则是空的。示例：bash(git status*) 或 edit_file。"
    label = rule.strip()

    def mutate(permissions: dict) -> None:
        current = permissions.get(behavior)
        if not isinstance(current, list):
            current = []
        if label not in current:
            current.append(label)
        permissions[behavior] = current

    _update_local(workspace, mutate)
    return f"已写入本地{behavior}规则 {label}。"


def clear_local_rules(workspace: Path) -> str:
    def mutate(permissions: dict) -> None:
        for behavior in BEHAVIORS:
            permissions[behavior] = []

    _update_local(workspace, mutate)
    return "已清掉 .harness/settings.local.json 里的权限规则。项目 settings.json 还在。"
