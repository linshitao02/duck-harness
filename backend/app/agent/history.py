"""按会话记录文件改动前的内容，并按批次撤销。"""

import json
import uuid
from pathlib import Path

MAX_BLOB = 1_500_000
SKIP_PARTS = {"history", "backups", "tool-results"}


def _journal(workspace: Path, conversation_id: int | None) -> Path:
    name = "none" if conversation_id is None else str(conversation_id)
    return workspace / ".harness" / "history" / f"{name}.jsonl"


def _root(ctx) -> Path:
    return ctx.origin_workspace or ctx.workspace


def remember_change(ctx, path: Path) -> None:
    try:
        workspace = _root(ctx)
        resolved = path.resolve()
        relative_parts = resolved.relative_to(workspace.resolve()).parts
    except (OSError, ValueError):
        return
    if ".harness" in relative_parts and any(part in SKIP_PARTS for part in relative_parts):
        return
    existed = path.is_file()
    blob_path = ""
    note = ""
    if existed:
        try:
            data = path.read_bytes()
        except OSError:
            return
        if len(data) > MAX_BLOB:
            note = "文件太大，没有保存撤销副本"
        else:
            blob_dir = workspace / ".harness" / "history" / "blobs"
            blob_dir.mkdir(parents=True, exist_ok=True)
            blob = blob_dir / f"{uuid.uuid4().hex}.bin"
            blob.write_bytes(data)
            blob_path = str(blob)
    batch = ctx.history_batch or uuid.uuid4().hex[:8]
    record = {
        "batch": batch,
        "path": str(resolved),
        "existed": existed,
        "blob": blob_path,
        "note": note,
    }
    journal = _journal(workspace, ctx.conversation_id)
    journal.parent.mkdir(parents=True, exist_ok=True)
    with journal.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _records(workspace: Path, conversation_id: int | None) -> tuple[list[dict], set[str]]:
    journal = _journal(workspace, conversation_id)
    if not journal.is_file():
        return [], set()
    rows = []
    reverted = set()
    for line in journal.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if item.get("revert"):
            reverted.add(str(item["revert"]))
            continue
        if item.get("batch") and item.get("path"):
            rows.append(item)
    return rows, reverted


def _restore(record: dict) -> str:
    path = Path(record["path"])
    if record.get("note"):
        return f"{path}：{record['note']}"
    if not record.get("existed"):
        if path.is_file():
            path.unlink()
        return f"已删除 {path}"
    blob = Path(record.get("blob") or "")
    if not blob.is_file():
        return f"找不到 {path} 的撤销副本"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob.read_bytes())
    return f"已恢复 {path}"


def rewind(workspace: Path, conversation_id: int | None, batches: int = 1) -> list[str]:
    rows, reverted = _records(workspace, conversation_id)
    grouped: list[tuple[str, list[dict]]] = []
    for record in rows:
        batch = str(record["batch"])
        if batch in reverted:
            continue
        if grouped and grouped[-1][0] == batch:
            grouped[-1][1].append(record)
        else:
            grouped.append((batch, [record]))
    if not grouped:
        return []
    chosen = grouped[-max(1, batches) :]
    restored = []
    journal = _journal(workspace, conversation_id)
    with journal.open("a", encoding="utf-8") as handle:
        for batch, records in reversed(chosen):
            for record in reversed(records):
                restored.append(_restore(record))
            handle.write(json.dumps({"revert": batch}, ensure_ascii=False) + "\n")
    return restored


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _cited_blobs(journal: Path, blob_root: Path) -> list[Path]:
    cited = []
    if not journal.is_file():
        return cited
    try:
        lines = journal.read_text(encoding="utf-8").splitlines()
    except OSError:
        return cited
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        raw = str(item.get("blob") or "")
        if not raw:
            continue
        blob = Path(raw)
        if blob.is_file() and not blob.is_symlink() and _inside(blob, blob_root):
            cited.append(blob)
    return cited


def discard_journal(workspace: Path, conversation_id: int) -> bool:
    """删掉这条会话的撤销记录，以及没有其他会话还在引用的副本。"""
    root = workspace / ".harness" / "history"
    journal = root / f"{int(conversation_id)}.jsonl"
    if not journal.is_file() or journal.is_symlink() or not _inside(journal, root):
        return False
    blob_root = root / "blobs"
    cited = _cited_blobs(journal, blob_root)
    kept = set()
    for other in root.glob("*.jsonl"):
        if other.name == journal.name:
            continue
        for blob in _cited_blobs(other, blob_root):
            kept.add(blob.resolve())
    journal.unlink()
    for blob in cited:
        if blob.resolve() in kept or not blob.is_file():
            continue
        blob.unlink()
    return True


def sweep_history(workspace: Path, live_ids: set[int]) -> int:
    """清掉工作区里已经没有对应会话的撤销记录，以及没人引用的副本。"""
    root = workspace / ".harness" / "history"
    if not root.is_dir() or root.is_symlink():
        return 0
    removed = 0
    for journal in list(root.glob("*.jsonl")):
        if not journal.stem.isdigit():
            continue
        conversation_id = int(journal.stem)
        if conversation_id in live_ids:
            continue
        if discard_journal(workspace, conversation_id):
            removed += 1
    blob_root = root / "blobs"
    if blob_root.is_dir() and not blob_root.is_symlink():
        cited = set()
        for journal in root.glob("*.jsonl"):
            for blob in _cited_blobs(journal, blob_root):
                cited.add(blob.resolve())
        for blob in blob_root.iterdir():
            if not blob.is_file() or blob.is_symlink() or blob.resolve() in cited:
                continue
            if _inside(blob, blob_root):
                blob.unlink()
    return removed
