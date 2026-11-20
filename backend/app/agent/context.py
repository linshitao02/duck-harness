"""把工作区里的项目说明和当前分支塞进系统提示。"""

from pathlib import Path

_FILES = ("HARNESS.md", "AGENTS.md", "CLAUDE.md", ".harness/HARNESS.md")


def workspace_brief(workspace: Path) -> dict[str, str]:
    chunks: list[str] = []
    budget = 8000
    for name in _FILES:
        path = workspace / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        piece = f"## {name}\n{text[:budget]}"
        chunks.append(piece)
        budget -= len(piece)
        if budget <= 0:
            break
    git = ""
    head = workspace / ".git" / "HEAD"
    if head.is_file():
        try:
            git = head.read_text(encoding="utf-8", errors="replace").strip()[:200]
        except OSError:
            git = ""
    return {"instructions": "\n\n".join(chunks), "git": git}
