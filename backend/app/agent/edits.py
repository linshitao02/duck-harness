"""文件替换的匹配细节：先完整读过、引号、换行、行号前缀。"""

import re
import time
from pathlib import Path

from .state import READS

FILE_UNCHANGED = (
    "文件自上次读取后没有改动。对话里上一次 read_file 的结果仍然有效，请直接用那次结果，不要再读一遍。"
)

_LINE_PREFIX = re.compile(r"^\s*\d+[|\t](.*)$")
_DESANITIZE = (
    ("<fnr>", "<function_results>"),
    ("<n>", "<name>"),
    ("</n>", "</name>"),
    ("<o>", "<output>"),
    ("</o>", "</output>"),
    ("<e>", "<error>"),
    ("</e>", "</error>"),
    ("<s>", "<system>"),
    ("</s>", "</system>"),
    ("<r>", "<result>"),
    ("</r>", "</result>"),
)


def path_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def remember_read(
    path: Path,
    content: str | None,
    partial: bool,
    *,
    reusable: bool = False,
    offset: int | None = None,
    limit: int | None = None,
) -> None:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    READS[path_key(path)] = {
        "content": None if partial else content,
        "mtime": mtime,
        "partial": partial,
        # 写入和片段读取不能用来抵下一次全文读取，否则模型会拿着旧正文继续改。
        "reusable": bool(reusable) and not partial,
        "offset": offset,
        "limit": limit,
        "read_at": time.time(),
    }


def unchanged_read(path: Path, offset: int, limit: int) -> str | None:
    """同一路径、同一行范围、磁盘修改时间没变时，不再把全文塞回上下文。"""
    stamp = READS.get(path_key(path))
    if not stamp or not stamp.get("reusable") or stamp.get("partial"):
        return None
    if stamp.get("offset") != offset or stamp.get("limit") != limit:
        return None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if mtime != stamp.get("mtime"):
        return None
    return FILE_UNCHANGED


def _same_text(left: str, right: str) -> bool:
    def norm(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")

    return norm(left) == norm(right)


def require_fresh_read(path: Path) -> str | None:
    stamp = READS.get(path_key(path))
    if not stamp or stamp.get("partial") or stamp.get("content") is None:
        return "还没有完整读过这个文件。请先用 read_file 读取后再改。"
    try:
        current = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"无法读取文件: {exc}"
    if not _same_text(current, str(stamp["content"])):
        return "文件在上次读取之后变过。请重新 read_file 后再改。"
    return None


def _strip_trailing_ws(text: str) -> str:
    parts = re.split(r"(\r\n|\n|\r)", text)
    out = []
    for index, part in enumerate(parts):
        if index % 2 == 0:
            out.append(re.sub(r"[ \t]+$", "", part))
        else:
            out.append(part)
    return "".join(out)


def _strip_line_prefixes(text: str) -> str:
    lines = text.splitlines()
    stripped = []
    changed = False
    for line in lines:
        match = _LINE_PREFIX.match(line)
        if match:
            stripped.append(match.group(1))
            changed = True
        else:
            stripped.append(line)
    if not changed:
        return text
    body = "\n".join(stripped)
    if text.endswith("\n"):
        body += "\n"
    return body


def _desanitize(text: str) -> str:
    for src, dst in _DESANITIZE:
        text = text.replace(src, dst)
    return text


def _normalize_quotes(text: str) -> str:
    return (
        text.replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )


def _opening(chars: list[str], index: int) -> bool:
    if index == 0:
        return True
    prev = chars[index - 1]
    return prev in " \t\r\n([{\u2014\u2013"


def _apply_curly(text: str, single: bool, double: bool) -> str:
    chars = list(text)
    out: list[str] = []
    for index, char in enumerate(chars):
        if double and char == '"':
            out.append("\u201c" if _opening(chars, index) else "\u201d")
        elif single and char == "'":
            prev = chars[index - 1] if index else ""
            nxt = chars[index + 1] if index + 1 < len(chars) else ""
            if prev.isalpha() and nxt.isalpha():
                out.append("\u2019")
            else:
                out.append("\u2018" if _opening(chars, index) else "\u2019")
        else:
            out.append(char)
    return "".join(out)


def find_actual(content: str, search: str) -> str | None:
    if search in content:
        return search
    folded_search = _normalize_quotes(search)
    folded = _normalize_quotes(content)
    index = folded.find(folded_search)
    if index == -1:
        return None
    return content[index : index + len(search)]


def _preserve_quotes(requested: str, actual: str, new: str) -> str:
    if requested == actual:
        return new
    double = "\u201c" in actual or "\u201d" in actual
    single = "\u2018" in actual or "\u2019" in actual
    if not double and not single:
        return new
    return _apply_curly(new, single, double)


def resolve_edit(content: str, old: str, new: str, path: Path) -> tuple[str, str] | None:
    prepared = new if path.suffix.lower() in {".md", ".mdx"} else _strip_trailing_ws(new)
    options: list[tuple[str, bool, bool]] = []

    def add(text: str, desanitize_new: bool, crlf: bool) -> None:
        item = (text, desanitize_new, crlf)
        if text and item not in options:
            options.append(item)

    sources = (
        (old, False),
        (_desanitize(old), True),
        (_strip_line_prefixes(old), False),
        (_desanitize(_strip_line_prefixes(old)), True),
    )
    for source, desanitize_new in sources:
        add(source, desanitize_new, False)
        if "\r\n" in content and "\n" in source and "\r\n" not in source:
            add(source.replace("\n", "\r\n"), desanitize_new, True)
    for candidate, desanitize_new, crlf in options:
        if candidate in content:
            aligned = prepared.replace("\n", "\r\n") if crlf else prepared
            if desanitize_new:
                aligned = _desanitize(aligned)
            return candidate, aligned
    for candidate, desanitize_new, crlf in options:
        actual = find_actual(content, candidate)
        if not actual:
            continue
        aligned = prepared.replace("\n", "\r\n") if crlf or ("\r\n" in actual and "\r\n" not in candidate) else prepared
        if desanitize_new:
            aligned = _desanitize(aligned)
        return actual, _preserve_quotes(candidate, actual, aligned)
    return None


def apply_once(content: str, old: str, new: str, replace_all: bool) -> str:
    target = old
    replacement = new
    if replacement == "" and not old.endswith("\n") and (old + "\n") in content:
        target = old + "\n"
    if replace_all:
        return content.replace(target, replacement)
    return content.replace(target, replacement, 1)


def change_snippet(updated: str, new: str, start_at: int) -> str:
    lines = updated.splitlines()
    span = max(1, new.count("\n") + 1)
    begin = max(1, start_at - 2)
    end = min(len(lines), start_at + span + 1)
    if not lines:
        return "（文件现在是空的）"
    return "\n".join(f"{number}|{lines[number - 1]}" for number in range(begin, end + 1))
