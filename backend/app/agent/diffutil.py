"""生成和拆出统一 diff，供模型和对话界面使用。"""

import difflib


def unified(path: str, before: str, after: str, limit: int = 80) -> str:
    lines = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=path,
            tofile=path,
            lineterm="",
        )
    )
    if not lines:
        return ""
    if len(lines) > limit:
        hidden = len(lines) - limit
        lines = lines[:limit] + [f"... 还有 {hidden} 行"]
    return "\n".join(lines)


def split_embedded_diff(content: str) -> tuple[str, str]:
    marker = "```diff\n"
    start = content.find(marker)
    if start < 0:
        return content, ""
    rest = content[start + len(marker) :]
    end = rest.find("\n```")
    if end < 0:
        return content, ""
    diff = rest[:end]
    shown = (content[:start] + rest[end + 4 :]).strip()
    return shown, diff


def attach_diff(text: str, diff: str) -> str:
    if not diff or "```diff" in (text or ""):
        return text or ""
    head, sep, tail = (text or "").partition("\n")
    block = "```diff\n" + diff + "\n```"
    if not sep:
        return (head + "\n" + block).strip()
    return head + "\n" + block + "\n" + tail
