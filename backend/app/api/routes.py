import json
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agent.group_run import iter_group_events
from ..agent.intake import answer_intake, close_intake, start_intake
from ..agent.loop import cancel_turn, iter_events
from ..agent.providers import OpenAICompatProvider, collect_text, hub
from ..agent.tools import broker, load_skills
from ..config import UPLOAD_DIR, get_settings
from ..db import get_engine, open_session
from ..models import Attachment, Conversation, McpServer, Message, Skill, Task
from ..services.runtime import (
    all_settings,
    compact_conversation,
    create_group,
    delete_conversation,
    doctor,
    list_conversations,
    present_messages,
    private_threads,
    deepseek_credentials,
    public_settings,
    serialize_attachment,
    serialize_conversation,
    PERMISSION_MODES,
    canonical_mode,
    set_settings,
    usage_summary,
)

router = APIRouter(prefix="/api")


def get_db():
    get_engine()
    db = open_session()
    try:
        yield db
    finally:
        db.close()


class ConversationIn(BaseModel):
    title: str = "新会话"
    provider: str | None = None
    model: str | None = None
    icon_key: str | None = None
    icon_color: str | None = None


class ConversationPatch(BaseModel):
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    workspace: str | None = None
    icon_key: str | None = None
    icon_color: str | None = None
    pinned: bool | None = None
    unread: bool | None = None
    hidden: bool | None = None
    group_name: str | None = None


class GroupMemberIn(BaseModel):
    name: str
    duty: str = ""
    icon_key: str | None = None
    icon_color: str | None = None


class GroupIn(BaseModel):
    title: str = ""
    members: list[GroupMemberIn] = Field(default_factory=list)
    conversation_ids: list[int] = Field(default_factory=list)
    icon_key: str | None = None
    icon_color: str | None = None


class ChatIn(BaseModel):
    content: str = ""
    attachment_ids: list[int] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None


class IntakeAnswerIn(BaseModel):
    message_id: int
    choice: str = ""
    text: str = ""


class ApprovalIn(BaseModel):
    approved: bool = False
    answers: dict | None = None


class RewindIn(BaseModel):
    batches: int = 1


class MemoryIn(BaseModel):
    title: str = ""
    content: str
    type: str = "user"
    description: str = ""


class SkillIn(BaseModel):
    name: str
    description: str = ""
    body: str = ""


class McpIn(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class ProviderTestIn(BaseModel):
    provider: str
    model: str | None = None


class TaskIn(BaseModel):
    title: str
    detail: str = ""
    status: str = "pending"
    conversation_id: int | None = None


def sse(payload: dict) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


@router.get("/health")
def health(db: Session = Depends(get_db)):
    from ..db import database_info

    info = database_info()
    report = {
        "service": "duck-harness",
        "database": info["database"],
        "reason": info["reason"],
        "detail": info["detail"],
        "mysql": False,
        "sqlite": False,
    }
    try:
        from sqlalchemy import text

        db.execute(text("SELECT 1"))
        report[info["database"]] = True
    except Exception as exc:
        report["error"] = str(exc)
    return report


@router.get("/me")
def me(db: Session = Depends(get_db)):
    return public_settings(db)["user"]


@router.get("/settings")
def read_settings(db: Session = Depends(get_db)):
    return public_settings(db)


@router.put("/settings")
def write_settings(body: dict, db: Session = Depends(get_db)):
    values = body.get("values") if isinstance(body.get("values"), dict) else body
    set_settings(db, values)
    return public_settings(db)


@router.get("/conversations")
def conversations(q: str = "", db: Session = Depends(get_db)):
    return {"items": list_conversations(db, q)}


@router.post("/conversations")
def create_conversation(body: ConversationIn, db: Session = Depends(get_db)):
    stored = all_settings(db)
    provider = body.provider or stored["provider"]
    model = body.model or (stored["deepseek_model"] if provider == "deepseek" else stored["ollama_model"])
    if body.model:
        model = body.model
    row = Conversation(
        title=body.title or "新会话",
        preview="",
        provider=provider,
        model=model,
        permission_mode=stored["permission_mode"],
        workspace=stored["workspace"],
        icon_key=(body.icon_key or "spark")[:32],
        icon_color=(body.icon_color or "#ff4b8d")[:16],
    )
    db.add(row)
    db.commit()
    return serialize_conversation(row)


@router.post("/groups")
def create_group_chat(body: GroupIn, db: Session = Depends(get_db)):
    try:
        row = create_group(
            db,
            body.title,
            [item.model_dump() for item in body.members],
            body.conversation_ids,
            body.icon_key,
            body.icon_color,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return serialize_conversation(row)


@router.patch("/conversations/{conversation_id}")
def patch_conversation(conversation_id: int, body: ConversationPatch, db: Session = Depends(get_db)):
    row = db.get(Conversation, conversation_id)
    if row is None:
        raise HTTPException(404, "会话不存在")
    data = body.model_dump(exclude_none=True)
    if "permission_mode" in data:
        data["permission_mode"] = canonical_mode(data["permission_mode"])
        if data["permission_mode"] not in PERMISSION_MODES:
            raise HTTPException(400, "权限模式无效")
    if "provider" in data and data["provider"] not in {"deepseek", "ollama"}:
        raise HTTPException(400, "模型提供方无效")
    for key, value in data.items():
        setattr(row, key, value)
    from datetime import datetime

    row.updated_at = datetime.now()
    db.commit()
    return serialize_conversation(row)


@router.delete("/conversations/{conversation_id}")
def remove_conversation(conversation_id: int, db: Session = Depends(get_db)):
    row = db.get(Conversation, conversation_id)
    if row is None:
        raise HTTPException(404, "会话不存在")
    delete_conversation(db, row)
    return {"ok": True}


@router.get("/conversations/{conversation_id}/messages")
def messages(conversation_id: int, db: Session = Depends(get_db)):
    row = db.get(Conversation, conversation_id)
    if row is None:
        raise HTTPException(404, "会话不存在")
    return {"items": present_messages(db, conversation_id), "private_threads": private_threads(db, row)}


@router.post("/conversations/{conversation_id}/intake")
async def begin_intake(conversation_id: int, db: Session = Depends(get_db)):
    row = db.get(Conversation, conversation_id)
    if row is None:
        raise HTTPException(404, "会话不存在")
    try:
        return await start_intake(db, row)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/conversations/{conversation_id}/intake/answer")
async def reply_intake(conversation_id: int, body: IntakeAnswerIn, db: Session = Depends(get_db)):
    row = db.get(Conversation, conversation_id)
    if row is None:
        raise HTTPException(404, "会话不存在")
    try:
        return await answer_intake(db, row, body.message_id, body.choice, body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/conversations/{conversation_id}/chat")
async def chat(conversation_id: int, body: ChatIn):
    get_engine()
    db = open_session()
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        db.close()
        raise HTTPException(404, "会话不存在")
    if body.provider in {"deepseek", "ollama"}:
        conversation.provider = body.provider
    if body.model:
        conversation.model = body.model
    if (conversation.kind or "chat") == "chat":
        close_intake(db, conversation)
    db.commit()

    async def generate():
        try:
            stream = (
                iter_group_events(db, conversation, body.content)
                if (conversation.kind or "") == "group"
                else iter_events(db, conversation, body.content, body.attachment_ids)
            )
            async for event in stream:
                yield sse(event)
        finally:
            db.close()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/conversations/{conversation_id}/cancel")
def cancel(conversation_id: int):
    cancel_turn(conversation_id)
    return {"ok": True}


@router.post("/conversations/{conversation_id}/compact")
async def compact(conversation_id: int, db: Session = Depends(get_db)):
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(404, "会话不存在")
    summary = await compact_conversation(db, conversation, hub)
    return {"summary": summary}


@router.post("/conversations/{conversation_id}/rewind")
def rewind_conversation(conversation_id: int, body: RewindIn, db: Session = Depends(get_db)):
    from ..agent.history import rewind

    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(404, "会话不存在")
    stored = all_settings(db)
    workspace = Path(conversation.workspace or stored["workspace"])
    restored = rewind(workspace, conversation.id, max(1, min(int(body.batches or 1), 20)))
    if not restored:
        return {"restored": [], "message": "没有可撤销的文件改动"}
    note = "已撤销这些文件改动：\n" + "\n".join(restored)
    db.add(Message(conversation_id=conversation.id, role="assistant", content=note, status="done"))
    db.commit()
    return {"restored": restored, "message": note}


@router.post("/conversations/{conversation_id}/attachments")
async def upload(conversation_id: int, file: UploadFile = File(...), db: Session = Depends(get_db)):
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(404, "会话不存在")
    folder = UPLOAD_DIR / str(conversation_id)
    folder.mkdir(parents=True, exist_ok=True)
    filename = Path(file.filename or "attachment").name
    target = folder / filename
    data = await file.read()
    target.write_bytes(data)
    row = Attachment(
        conversation_id=conversation_id,
        filename=filename,
        mime=file.content_type or "application/octet-stream",
        size=len(data),
        path=str(target),
    )
    db.add(row)
    db.commit()
    return serialize_attachment(row)


@router.get("/attachments/{attachment_id}")
def download(attachment_id: int, db: Session = Depends(get_db)):
    row = db.get(Attachment, attachment_id)
    if row is None or not Path(row.path).exists():
        raise HTTPException(404, "附件不存在")
    return FileResponse(row.path, filename=row.filename, media_type=row.mime)


@router.post("/approvals/{approval_id}")
def approve(approval_id: str, body: ApprovalIn):
    if not broker.resolve(approval_id, body.approved, body.answers):
        raise HTTPException(404, "没有这条待确认操作")
    return {"ok": True}


def _memory_workspace(db: Session) -> Path:
    return Path(all_settings(db)["workspace"])


def _memory_item(record: dict) -> dict:
    return {
        "id": record["slug"],
        "title": record["name"],
        "content": record["body"],
        "type": record["type"],
        "type_label": record["type_label"],
        "description": record["description"],
        "age": record["age"],
    }


@router.get("/memories")
def memories(db: Session = Depends(get_db)):
    from ..agent.memory import _records, import_legacy

    workspace = _memory_workspace(db)
    import_legacy(db, workspace)
    return {"items": [_memory_item(item) for item in _records(workspace)]}


@router.post("/memories")
def add_memory(body: MemoryIn, db: Session = Depends(get_db)):
    from ..agent.memory import save_memory

    if not body.content.strip():
        raise HTTPException(400, "内容是空的")
    try:
        saved = save_memory(
            _memory_workspace(db),
            body.title or body.content[:40],
            body.content.strip(),
            body.type or "user",
            body.description,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _memory_item(saved)


@router.delete("/memories/{memory_id}")
def remove_memory(memory_id: str, db: Session = Depends(get_db)):
    from ..agent.memory import forget_memory

    try:
        forget_memory(_memory_workspace(db), memory_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@router.get("/tasks")
def tasks(conversation_id: int | None = None, db: Session = Depends(get_db)):
    from sqlalchemy import select

    statement = select(Task).order_by(Task.id.asc())
    if conversation_id:
        statement = statement.where(Task.conversation_id == conversation_id)
    rows = db.scalars(statement).all()
    return {
        "items": [
            {
                "id": row.id,
                "conversation_id": row.conversation_id,
                "title": row.title,
                "detail": row.detail,
                "status": row.status,
            }
            for row in rows
        ]
    }


@router.post("/tasks")
def add_task(body: TaskIn, db: Session = Depends(get_db)):
    row = Task(
        conversation_id=body.conversation_id,
        title=body.title,
        detail=body.detail,
        status=body.status,
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "title": row.title, "status": row.status}


@router.get("/skills")
def skills(db: Session = Depends(get_db)):
    workspace = Path(all_settings(db)["workspace"])
    return {"items": load_skills(db, workspace)}


@router.post("/skills")
def add_skill(body: SkillIn, db: Session = Depends(get_db)):
    row = db.query(Skill).filter(Skill.name == body.name).one_or_none()
    if row is None:
        row = Skill(name=body.name, description=body.description, body=body.body, enabled=True)
        db.add(row)
    else:
        row.description = body.description
        row.body = body.body
        row.enabled = True
    db.commit()
    return {"name": row.name}


@router.get("/mcp")
def mcp_list(db: Session = Depends(get_db)):
    from sqlalchemy import select

    rows = db.scalars(select(McpServer).order_by(McpServer.id.asc())).all()
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "command": row.command,
                "args": row.args or [],
                "enabled": row.enabled,
            }
            for row in rows
        ]
    }


@router.post("/mcp")
def mcp_add(body: McpIn, db: Session = Depends(get_db)):
    row = McpServer(name=body.name, command=body.command, args=body.args, env=body.env, enabled=True)
    db.add(row)
    db.commit()
    return {"id": row.id, "name": row.name}


@router.get("/mcp/status")
async def mcp_status(db: Session = Depends(get_db)):
    from ..agent.mcp_bridge import refresh, status_payload

    await refresh(db, force=True)
    return status_payload()


@router.delete("/mcp/{server_id}")
def mcp_delete(server_id: int, db: Session = Depends(get_db)):
    row = db.get(McpServer, server_id)
    if row is None:
        raise HTTPException(404, "没有这个 MCP 服务")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/usage")
def usage(db: Session = Depends(get_db)):
    return usage_summary(db)


@router.get("/doctor")
async def doctor_route(db: Session = Depends(get_db)):
    return await doctor(db)


@router.get("/models")
async def models(db: Session = Depends(get_db)):
    stored = all_settings(db)
    ollama_models: list[str] = []
    ollama_ok = False
    base = (stored.get("ollama_base") or get_settings().ollama_base_url).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(base + "/api/tags")
            response.raise_for_status()
            ollama_models = [item.get("name", "") for item in response.json().get("models", []) if item.get("name")]
            ollama_ok = True
    except Exception:
        ollama_ok = False
    _base, deepseek_key, _source = deepseek_credentials(db)
    return {
        "active": {"provider": stored["provider"], "model": stored["model"]},
        "providers": [
            {
                "id": "deepseek",
                "label": "DeepSeek",
                "configured": bool(deepseek_key),
                "models": ["deepseek-chat", "deepseek-reasoner"] if deepseek_key else [],
            },
            {
                "id": "ollama",
                "label": "Ollama",
                "configured": ollama_ok and bool(ollama_models),
                "models": ollama_models if ollama_ok else [],
            },
        ],
    }


@router.post("/providers/test")
async def provider_test(body: ProviderTestIn, db: Session = Depends(get_db)):
    stored = all_settings(db)
    if body.provider == "ollama":
        base = (stored.get("ollama_base") or get_settings().ollama_base_url).rstrip("/") + "/v1"
        model = body.model or stored.get("ollama_model") or "qwen3:4b"
        provider = OpenAICompatProvider(base, "ollama", "ollama")
        prompt = "只输出两个字：收到 /no_think"
    elif body.provider == "deepseek":
        model = body.model or stored.get("deepseek_model") or "deepseek-chat"
        base, key, _source = deepseek_credentials(db)
        if not key:
            raise HTTPException(400, "还没有配置 DeepSeek 密钥")
        provider = OpenAICompatProvider(base, key, "deepseek")
        prompt = "只回复两个字：收到。不要调用工具。"
    else:
        raise HTTPException(400, "未知的模型提供方")
    try:
        text = await collect_text(provider, model, prompt, tools=None, max_tokens=64)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    if not text.strip():
        raise HTTPException(502, "模型没有返回文本")
    return {"ok": True, "provider": body.provider, "model": model, "text": text[:500]}
