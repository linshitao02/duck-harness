import json
from pathlib import Path

from app.config import UPLOAD_DIR
from app.db import open_session
from app.models import Attachment, Conversation
from app.services.runtime import sweep_orphaned_conversation_files


def _journal(workspace: Path, conversation_id: int, blob_name: str, project: Path) -> Path:
    history = workspace / ".harness" / "history"
    blobs = history / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    blob = blobs / blob_name
    blob.write_bytes(b"old-copy")
    journal = history / f"{conversation_id}.jsonl"
    record = {"batch": "b1", "path": str(project), "existed": True, "blob": str(blob), "note": ""}
    journal.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    return journal


def test_delete_removes_uploads_and_undo_copies(client):
    created = client.post("/api/conversations", json={"title": "待清理"}).json()
    conversation_id = created["id"]
    folder = UPLOAD_DIR / str(conversation_id)
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("附件", encoding="utf-8")
    db = open_session()
    try:
        conversation = db.get(Conversation, conversation_id)
        workspace = Path(conversation.workspace)
    finally:
        db.close()
    project = workspace / "keep.txt"
    project.write_text("现在的内容", encoding="utf-8")
    journal = _journal(workspace, conversation_id, "undo.bin", project)
    blob = workspace / ".harness" / "history" / "blobs" / "undo.bin"

    removed = client.delete(f"/api/conversations/{conversation_id}")
    assert removed.status_code == 200
    assert not folder.exists()
    assert not journal.exists()
    assert not blob.exists()
    assert project.read_text(encoding="utf-8") == "现在的内容"


def test_sweep_removes_only_orphans(client):
    created = client.post("/api/conversations", json={"title": "还在"}).json()
    live_id = created["id"]
    live_folder = UPLOAD_DIR / str(live_id)
    live_folder.mkdir(parents=True)
    live_file = live_folder / "live.txt"
    live_file.write_text("留下", encoding="utf-8")
    (live_folder / "stray.txt").write_text("落单", encoding="utf-8")
    db = open_session()
    try:
        db.add(Attachment(conversation_id=live_id, filename="live.txt", path=str(live_file), size=live_file.stat().st_size))
        db.commit()
    finally:
        db.close()
    orphan_id = 9_000_000 + live_id
    orphan_folder = UPLOAD_DIR / str(orphan_id)
    orphan_folder.mkdir(parents=True)
    (orphan_folder / "gone.txt").write_text("遗留", encoding="utf-8")
    db = open_session()
    try:
        conversation = db.get(Conversation, live_id)
        workspace = Path(conversation.workspace)
    finally:
        db.close()
    project = workspace / "project.txt"
    project.write_text("项目文件", encoding="utf-8")
    live_journal = _journal(workspace, live_id, "live.bin", project)
    orphan_journal = _journal(workspace, orphan_id, "orphan.bin", project)
    loose = workspace / ".harness" / "history" / "blobs" / "loose.bin"
    loose.write_bytes(b"nobody")
    untouched = workspace / ".harness" / "history" / "none.jsonl"
    untouched.write_text("{}\n", encoding="utf-8")

    db = open_session()
    try:
        sweep_orphaned_conversation_files(db)
    finally:
        db.close()

    assert (live_folder / "live.txt").read_text(encoding="utf-8") == "留下"
    assert not (live_folder / "stray.txt").exists()
    assert not orphan_folder.exists()
    assert live_journal.is_file()
    assert (workspace / ".harness" / "history" / "blobs" / "live.bin").is_file()
    assert not orphan_journal.exists()
    assert not (workspace / ".harness" / "history" / "blobs" / "orphan.bin").exists()
    assert not loose.exists()
    assert untouched.read_text(encoding="utf-8") == "{}\n"
    assert project.read_text(encoding="utf-8") == "项目文件"


def test_group_delete_keeps_files_of_linked_chats(client):
    first = client.post("/api/conversations", json={"title": "项目经理"}).json()
    second = client.post("/api/conversations", json={"title": "实现工程师"}).json()
    linked = client.post("/api/groups", json={"title": "链接群", "conversation_ids": [first["id"], second["id"]]}).json()
    synthetic = client.post(
        "/api/groups",
        json={"title": "临时群", "members": [{"name": "协调者", "duty": "派活"}, {"name": "设计", "duty": "出方案"}]},
    ).json()
    members = [
        item
        for item in client.get("/api/conversations").json()["items"]
        if item.get("kind") == "member" and item.get("room_id") == synthetic["id"]
    ]
    assert len(members) == 2
    for conversation_id in (first["id"], members[0]["id"]):
        folder = UPLOAD_DIR / str(conversation_id)
        folder.mkdir(parents=True)
        (folder / "file.txt").write_text(str(conversation_id), encoding="utf-8")

    assert client.delete(f"/api/conversations/{linked['id']}").status_code == 200
    assert client.delete(f"/api/conversations/{synthetic['id']}").status_code == 200
    assert (UPLOAD_DIR / str(first["id"]) / "file.txt").is_file()
    assert not (UPLOAD_DIR / str(members[0]["id"])).exists()
