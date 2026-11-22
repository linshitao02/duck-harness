import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.agent.loop import iter_events
from app.agent.providers import ModelFallback, OpenAICompatProvider, ScriptedProvider, collect_text, hub
from app.agent.safety import blocked_command, sensitive_reason
from app.agent.shellguard import command_is_readonly
from app.agent import mcp_client
from app.agent.mcp_bridge import find_tool, prompt_text, public_tool_name, refresh
from app.agent.permissions import add_local_rule
from app.agent.tools import broker, execute, schemas
from app.db import Base
from app.models import Conversation, Message, McpServer, Schedule, Setting, Task, UsageEvent
from app.services.runtime import compact_conversation, repair_workspaces
from tests.conftest import events_from


def test_tables_exist():
    expected = {
        "users",
        "conversations",
        "messages",
        "attachments",
        "memories",
        "tasks",
        "skills",
        "settings",
        "mcp_servers",
        "schedules",
        "usage_events",
    }
    assert set(Base.metadata.tables) == expected


def test_missing_workspace_follows_renamed_folder(db):
    from app.models import Setting
    from app.services.runtime import repair_workspaces

    missing = r"C:\Users\shita\AI\agent\cc-harness"
    setting = db.get(Setting, "workspace")
    setting.value = missing
    conversation = Conversation(title="旧目录", provider="deepseek", model="deepseek-chat", workspace=missing)
    db.add(conversation)
    db.commit()
    repaired = repair_workspaces(db)
    db.refresh(conversation)
    db.refresh(setting)
    assert Path(repaired).is_dir()
    assert conversation.workspace == repaired
    assert setting.value == repaired
    assert not repaired.lower().endswith("cc-harness")


def test_missing_workspace_is_repaired(db):
    missing = r"C:\no\such\cc-harness"
    stored = db.get(Setting, "workspace")
    stored.value = missing
    conversation = Conversation(title="旧目录", provider="deepseek", model="deepseek-chat", workspace=missing)
    db.add(conversation)
    db.commit()
    repaired = repair_workspaces(db)
    db.refresh(conversation)
    db.refresh(stored)
    assert Path(repaired).is_dir()
    assert conversation.workspace == repaired
    assert stored.value == repaired
    assert "cc-harness" not in repaired.replace("/", "\\")


def test_main_turn_is_not_cut_off_at_twelve_steps(db, tmp_path):
    from app.agent.providers import StreamEvent

    class ManyTools:
        def __init__(self):
            self.foreground = 0

        async def stream(self, messages, tools, model, should_stop=None, max_tokens=None, query_source="foreground", fallback_model=""):
            del messages, tools, model, should_stop, max_tokens, fallback_model
            if query_source != "foreground":
                yield StreamEvent(kind="text", text="旁路摘要")
                yield StreamEvent(kind="usage", prompt_tokens=1, completion_tokens=1)
                return
            self.foreground += 1
            if self.foreground <= 201:
                yield StreamEvent(
                    kind="tool_call",
                    tool_id=f"c{self.foreground}",
                    tool_name="system_info",
                    arguments_delta="{}",
                )
            else:
                yield StreamEvent(kind="text", text="两百步之后仍然完成")
            yield StreamEvent(kind="usage", prompt_tokens=1, completion_tokens=1)

    async def scenario():
        stored = db.get(Setting, "max_steps")
        stored.value = "12"
        db.commit()
        conversation = Conversation(
            title="步数",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(tmp_path),
        )
        db.add(conversation)
        db.commit()
        provider = ManyTools()
        hub.override = provider
        async for _event in iter_events(db, conversation, "连续查看", []):
            pass
        bodies = [row.content for row in db.query(Message).filter(Message.conversation_id == conversation.id)]
        assert "两百步之后仍然完成" in bodies
        assert provider.foreground == 202

    asyncio.run(scenario())


def test_safety_blocks_secrets_and_disk_wipe():
    assert sensitive_reason(Path(r"C:\Users\shita\Desktop\账号密码\deepseek-key.txt"))
    assert blocked_command(r"Get-Content C:\Users\shita\Desktop\账号密码\deepseek-key.txt")
    assert blocked_command("format C:")
    assert blocked_command("format.com D:")
    assert blocked_command('Get-Date -Format "yyyy-MM-dd"') is None
    assert blocked_command("Format-Table -AutoSize") is None
    assert blocked_command("ft -AutoSize") is None
    assert blocked_command('echo "shutdown"') is None
    assert blocked_command("echo shutdown") is None
    assert blocked_command("Select-String -Pattern shutdown") is None
    assert blocked_command("echo , format C:")
    assert blocked_command('Write-Output "format C:"') is None
    assert blocked_command("Get-Process | Format-Table -AutoSize") is None
    assert blocked_command("shutdown /s /t 0")
    assert blocked_command('cmd /c "format C:"')
    assert blocked_command("powershell -EncodedCommand QQ==")
    assert blocked_command("Remove-Item -Recurse C:\\")
    assert blocked_command(r"Remove-Item -Recurse C:\Windows\System32")
    assert blocked_command("rm -rf /")
    assert blocked_command("Remove-Item .\\notes.txt") is None


def test_readonly_commands_follow_parsed_allowlist():
    assert command_is_readonly('Get-Date -Format "yyyy-MM-dd"')
    assert command_is_readonly("Get-ChildItem | Format-Table -AutoSize")
    assert command_is_readonly("ls | ft Name")
    assert command_is_readonly("Get-Process | Select-Object Name, CPU")
    assert command_is_readonly("Where-Object Name -eq notes")
    assert command_is_readonly("echo shutdown")
    assert command_is_readonly("Select-String -Pattern shutdown")
    assert command_is_readonly('git log --format=%h')
    assert command_is_readonly("ipconfig /all")
    assert not command_is_readonly("Where-Object { $_.Name -eq 'a' }")
    assert not command_is_readonly("Format-Table @{N='x';E={1}}")
    assert not command_is_readonly("Format-Table $env:SECRET")
    assert not command_is_readonly("Remove-Item a.txt")
    assert not command_is_readonly("git branch -d foo")
    assert not command_is_readonly("ipconfig /flushdns")
    assert not command_is_readonly("route add 10.0.0.0 mask 255.0.0.0 192.168.1.1")
    assert command_is_readonly("route print")


def test_health_settings_and_conversation_crud(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["database"] in {"mysql", "sqlite"}
    assert body[body["database"]] is True
    assert body["mysql"] is (body["database"] == "mysql")
    assert body["sqlite"] is (body["database"] == "sqlite")

    created = client.post("/api/conversations", json={"title": "验收会话"}).json()
    listed = client.get("/api/conversations").json()["items"]
    assert any(item["id"] == created["id"] for item in listed)
    found = client.get("/api/conversations", params={"q": "验收"}).json()["items"]
    assert found and found[0]["title"] == "验收会话"

    renamed = client.patch(f"/api/conversations/{created['id']}", json={"title": "改名了"}).json()
    assert renamed["title"] == "改名了"
    marked = client.patch(
        f"/api/conversations/{created['id']}",
        json={"pinned": True, "unread": True, "hidden": True, "group_name": "工作"},
    ).json()
    assert marked["pinned"] is True and marked["unread"] is True and marked["hidden"] is True
    assert marked["group_name"] == "工作"
    styled = client.post(
        "/api/conversations",
        json={"title": "新 Bot", "icon_key": "heart", "icon_color": "#f5c542"},
    ).json()
    assert styled["icon_key"] == "heart" and styled["icon_color"] == "#f5c542" and styled["pinned"] is False
    assert client.delete(f"/api/conversations/{created['id']}").json()["ok"] is True
    assert client.get(f"/api/conversations/{created['id']}/messages").status_code == 404

    settings = client.get("/api/settings").json()
    assert settings["user"]["name"] == "用户"
    assert settings["deepseek_configured"] is True
    assert "deepseek_api_key" not in settings["values"]
    saved = client.put("/api/settings", json={"permission_mode": "plan", "user_name": "用户"}).json()
    assert saved["values"]["permission_mode"] == "plan"
    secret = client.put("/api/settings", json={"deepseek_api_key": "sk-settings-only", "deepseek_base": "https://example.invalid"}).json()
    assert "sk-settings-only" not in str(secret)
    assert secret["deepseek_configured"] is True
    assert secret["deepseek_key_source"] == "settings"
    assert secret["values"]["deepseek_base"] == "https://example.invalid"
    kept = client.put("/api/settings", json={"deepseek_api_key": ""}).json()
    assert kept["deepseek_key_source"] == "settings"
    cleared = client.put("/api/settings", json={"clear_deepseek_key": True}).json()
    assert cleared["deepseek_key_source"] == "env"
    assert cleared["deepseek_configured"] is True


def test_stream_and_usage(client, db):
    created = client.post("/api/conversations", json={"title": "流式"}).json()
    hub.override = ScriptedProvider([{"content": "流式回复已收到", "prompt_tokens": 9, "completion_tokens": 4}])
    with client.stream("POST", f"/api/conversations/{created['id']}/chat", json={"content": "你好"}) as response:
        assert response.status_code == 200
        payload = "".join(response.iter_text())
    kinds = [item["type"] for item in events_from(payload)]
    assert "token" in kinds and "done" in kinds
    assert "流式回复已收到" in payload
    messages = client.get(f"/api/conversations/{created['id']}/messages").json()["items"]
    assert messages[0]["role"] == "user"
    assert messages[1]["content"] == "流式回复已收到"
    usage = client.get("/api/usage").json()
    assert usage["prompt_tokens"] >= 9
    assert db.get(UsageEvent, 1) is not None or usage["completion_tokens"] >= 4


def test_separate_conversation_context(db, tmp_path):
    async def scenario():
        first = Conversation(
            title="甲",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(tmp_path),
        )
        second = Conversation(
            title="乙",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(tmp_path),
        )
        db.add_all([first, second])
        db.commit()
        scripted = ScriptedProvider(
            [{"content": "苹果已记下"}, {"content": "香蕉已记下"}, {"content": "还是苹果"}]
        )
        hub.override = scripted
        async for _event in iter_events(db, first, "请记住苹果", []):
            pass
        async for _event in iter_events(db, second, "请记住香蕉", []):
            pass
        async for _event in iter_events(db, first, "再说一次主题", []):
            pass
        blob = json.dumps(scripted.calls[2]["messages"], ensure_ascii=False)
        assert "苹果" in blob
        assert "香蕉" not in blob

    asyncio.run(scenario())


def test_bash_read_write_edit_search(ctx):
    async def scenario():
        written = await execute("write_file", {"path": "note.txt", "content": "alpha\nbeta\n"}, ctx)
        assert written.ok
        edited = await execute(
            "edit_file",
            {"path": "note.txt", "old": "beta", "new": "gamma"},
            ctx,
        )
        assert edited.ok
        (ctx.workspace / "note.txt").write_text("same\nsame\n", encoding="utf-8")
        seen = await execute("read_file", {"path": "note.txt"}, ctx)
        assert "same" in seen.text
        ambiguous = await execute("edit_file", {"path": "note.txt", "old": "same", "new": "x"}, ctx)
        assert not ambiguous.ok
        replaced = await execute(
            "edit_file",
            {"path": "note.txt", "old": "same", "new": "x", "replace_all": True},
            ctx,
        )
        assert replaced.ok
        read = await execute("read_file", {"path": "note.txt", "offset": 1, "limit": 5}, ctx)
        assert "1|x" in read.text
        (ctx.workspace / "keep.py").write_text("print('hello-marker')\n", encoding="utf-8")
        found = await execute("grep", {"pattern": "hello-marker"}, ctx)
        assert "keep.py" in found.text
        matched = await execute("glob_files", {"pattern": "*.py"}, ctx)
        assert "keep.py" in matched.text
        listing = await execute("list_dir", {"path": "."}, ctx)
        assert "note.txt" in listing.text
        ran = await execute("bash", {"command": "Write-Output hi-from-harness"}, ctx)
        assert ran.ok and "hi-from-harness" in ran.text
        denied = await execute(
            "bash",
            {"command": r"Get-Content C:\Users\shita\Desktop\账号密码\deepseek-key.txt"},
            ctx,
        )
        assert not denied.ok

    asyncio.run(scenario())


def test_documents_and_notebook(ctx):
    async def scenario():
        notebook = {
            "cells": [{"cell_type": "code", "source": ["print('cell-one')\n"]}],
            "nbformat": 4,
            "nbformat_minor": 5,
        }
        (ctx.workspace / "demo.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
        read_nb = await execute("read_file", {"path": "demo.ipynb"}, ctx)
        assert "cell-one" in read_nb.text
        from pypdf import PdfWriter

        pdf_path = ctx.workspace / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with pdf_path.open("wb") as handle:
            writer.write(handle)
        read_pdf = await execute("read_file", {"path": "blank.pdf"}, ctx)
        assert "第 1 页" in read_pdf.text

    asyncio.run(scenario())


def test_web_memory_task_skill_docx_schedule(ctx, db, monkeypatch):
    async def fake_search(query, limit=5):
        return [{"title": "结果", "url": "https://example.com", "snippet": query}]

    async def fake_fetch(url, limit=15000):
        return "页面正文 " + url

    monkeypatch.setattr("app.agent.tools.search_web", fake_search)
    monkeypatch.setattr("app.agent.tools.fetch_url", fake_fetch)

    async def scenario():
        conversation = Conversation(title="文档", provider="deepseek", model="deepseek-chat", workspace=str(ctx.workspace))
        db.add(conversation)
        db.commit()
        local = replace(ctx, conversation_id=conversation.id)
        searched = await execute("web_search", {"query": "harness"}, local)
        assert "example.com" in searched.text
        fetched = await execute("web_fetch", {"url": "https://example.com/docs"}, local)
        assert "页面正文" in fetched.text
        remembered = await execute(
            "remember",
            {"content": "用户喜欢中文回答", "title": "语言", "description": "用中文回答", "type": "user"},
            local,
        )
        assert remembered.ok
        assert (ctx.workspace / ".harness" / "memory" / "语言.md").is_file()
        listed = await execute("list_memories", {}, local)
        assert "语言" in listed.text and "用中文回答" in listed.text
        task = await execute("task_write", {"title": "核对清单", "status": "in_progress"}, local)
        assert task.ok
        listed = await execute("list_tasks", {}, local)
        assert "核对清单" in listed.text
        task_id = int(task.text.split("#", 1)[1].split()[0])
        updated = await execute("task_write", {"id": task_id, "status": "done"}, local)
        assert "done" in updated.text
        skill = await execute("use_skill", {"name": "review"}, local)
        assert "git_summary" in skill.text
        document = await execute(
            "generate_docx",
            {"title": "验收文档", "content": "第一段\n第二段", "filename": "验收.docx"},
            local,
        )
        assert document.ok and document.attachments
        from app.models import Attachment

        attachment = db.get(Attachment, document.attachments[0])
        read_doc = await execute("read_file", {"path": attachment.path}, local)
        assert "第一段" in read_doc.text
        scheduled = await execute("schedule_task", {"prompt": "明天提醒", "every_seconds": 120}, local)
        assert scheduled.ok
        assert db.query(Schedule).count() == 1
        info = await execute("system_info", {}, local)
        assert "python" in info.text
        git_text = await execute("git_summary", {}, local)
        assert "不是 git" in git_text.text or "git" in git_text.text.lower()

    asyncio.run(scenario())


def test_plan_and_ask_permissions(ctx):
    async def scenario():
        planned = replace(ctx, permission_mode="plan")
        blocked = await execute("write_file", {"path": "nope.txt", "content": "x"}, planned)
        assert not blocked.ok
        allowed = await execute("read_file", {"path": "missing.txt"}, planned)
        assert not allowed.ok and "不存在" in allowed.text

        asked = replace(ctx, permission_mode="ask")

        async def deny():
            while not broker.pending:
                await asyncio.sleep(0.01)
            approval_id = next(iter(broker.pending))
            broker.resolve(approval_id, False)

        waiter = asyncio.create_task(deny())
        denied = await execute("write_file", {"path": "ask.txt", "content": "x"}, asked)
        await waiter
        assert not denied.ok
        assert not (ctx.workspace / "ask.txt").exists()

    asyncio.run(scenario())


def test_orphan_tool_messages_are_dropped():
    from app.agent.loop import _without_orphan_tools

    cleaned = _without_orphan_tools(
        [
            {"role": "system", "content": "s"},
            {"role": "tool", "tool_call_id": "old", "content": "孤立的结果"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "new", "type": "function", "function": {"name": "web_fetch", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "new", "content": "正文"},
        ]
    )
    assert [item["role"] for item in cleaned] == ["system", "assistant", "tool"]
    assert cleaned[-1]["tool_call_id"] == "new"


def test_subagent_and_compact(db, tmp_path):
    async def scenario():
        conversation = Conversation(
            title="子代理",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(tmp_path),
        )
        db.add(conversation)
        db.commit()
        hub.override = ScriptedProvider(
            [
                {"content": "", "tool_calls": [{"id": "s1", "name": "spawn_agent", "arguments": {"task": "统计目录"}}]},
                {"content": "子任务完成"},
                {"content": "父代理已收到子任务完成"},
            ]
        )
        collected = []
        async for event in iter_events(db, conversation, "请派子代理看目录", []):
            collected.append(event)
        assert any(event["type"] == "done" and "父代理已收到" in event["content"] for event in collected)
        assert any(event["type"] == "tool_start" and event["name"] == "spawn_agent" for event in collected)

        for index in range(6):
            db.add(Message(conversation_id=conversation.id, role="user" if index % 2 == 0 else "assistant", content=f"历史{index}", status="done"))
        db.commit()
        hub.override = ScriptedProvider([{"content": "摘要：保留了历史路径"}])
        summary = await compact_conversation(db, conversation, hub)
        assert "摘要" in summary
        db.refresh(conversation)
        hidden = [row for row in db.query(Message).filter(Message.conversation_id == conversation.id) if not row.in_context]
        assert hidden

        for row in db.query(Message).filter(Message.conversation_id == conversation.id):
            row.in_context = True
        db.add(
            Message(
                conversation_id=conversation.id,
                role="assistant",
                content="",
                status="done",
                tool_calls=[{"id": "keep-me", "name": "web_fetch", "arguments": {"url": "https://example.com"}}],
            )
        )
        db.add(Message(conversation_id=conversation.id, role="tool", content="正文", status="done", tool_call_id="keep-me"))
        db.add(Message(conversation_id=conversation.id, role="tool", content="另一条", status="done", tool_call_id="keep-me"))
        db.add(Message(conversation_id=conversation.id, role="user", content="继续", status="done"))
        db.add(Message(conversation_id=conversation.id, role="assistant", content="好", status="done"))
        db.commit()
        hub.override = ScriptedProvider([{"content": "摘要：工具结果还在"}])
        await compact_conversation(db, conversation, hub)
        kept = [
            row
            for row in db.query(Message)
            .filter(Message.conversation_id == conversation.id, Message.in_context.is_(True))
            .order_by(Message.id.asc())
        ]
        assert kept[0].role == "assistant"
        assert kept[0].tool_calls
        assert any(row.role == "tool" and row.tool_call_id == "keep-me" for row in kept)

    asyncio.run(scenario())


def test_slash_doctor_mode_and_models(client):
    created = client.post("/api/conversations", json={"title": "命令"}).json()
    hub.override = ScriptedProvider([{"content": "不会用到"}])
    with client.stream("POST", f"/api/conversations/{created['id']}/chat", json={"content": "/mode plan"}) as response:
        body = "".join(response.iter_text())
    assert "plan" in body
    refreshed = client.get("/api/conversations", params={"q": "命令"}).json()["items"][0]
    assert refreshed["permission_mode"] == "plan"
    doctor = client.get("/api/doctor").json()
    names = {item["name"]: item for item in doctor["checks"]}
    database_check = names.get("mysql") or names["sqlite"]
    assert database_check["ok"] is True
    assert names["deepseek_key"]["ok"] is True
    assert names["python"]["ok"] is True
    models = client.get("/api/models").json()
    assert {item["id"] for item in models["providers"]} == {"deepseek", "ollama"}


def test_attachment_roundtrip(client, tmp_path):
    created = client.post("/api/conversations", json={"title": "附件"}).json()
    upload = client.post(
        f"/api/conversations/{created['id']}/attachments",
        files={"file": ("notes.txt", b"attachment-body", "text/plain")},
    )
    assert upload.status_code == 200
    attachment_id = upload.json()["id"]
    downloaded = client.get(f"/api/attachments/{attachment_id}")
    assert downloaded.content == b"attachment-body"
    hub.override = ScriptedProvider([{"content": "看到附件了"}])
    with client.stream(
        "POST",
        f"/api/conversations/{created['id']}/chat",
        json={"content": "看附件", "attachment_ids": [attachment_id]},
    ) as response:
        "".join(response.iter_text())
    messages = client.get(f"/api/conversations/{created['id']}/messages").json()["items"]
    assert messages[0]["attachments"]
    assert "notes.txt" in messages[0]["content"]


def test_mcp_echo(ctx, db):
    script = Path(__file__).resolve().parent / "mcp_echo.py"

    async def scenario():
        db.add(McpServer(name="echo", command=sys.executable, args=[str(script)], env={}, enabled=True))
        db.commit()
        listed = await execute("list_mcp_tools", {}, ctx)
        assert "echo" in listed.text
        called = await execute("call_mcp_tool", {"server": "echo", "tool": "echo", "arguments": {"text": "ping"}}, ctx)
        assert called.ok and "ping" in called.text
        resources = await execute("list_mcp_resources", {}, ctx)
        assert "echo://note" in resources.text
        resource = await execute("read_mcp_resource", {"server": "echo", "uri": "echo://note"}, ctx)
        assert resource.ok and "resource-ok" in resource.text

    asyncio.run(scenario())


def test_public_mcp_name_is_safe():
    import re

    assert public_tool_name("echo", "echo") == "mcp__echo__echo"
    ugly = public_tool_name("回显 服务", "do thing")
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ugly)
    assert ugly != "mcp__回显 服务__do thing"


def test_mcp_tools_are_builtin(ctx, db):
    script = Path(__file__).resolve().parent / "mcp_echo.py"

    async def scenario():
        db.add(McpServer(name="echo", command=sys.executable, args=[str(script)], env={}, enabled=True))
        conversation = Conversation(
            title="mcp",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(ctx.workspace),
        )
        db.add(conversation)
        db.commit()
        scripted = ScriptedProvider(
            [
                {"tool_calls": [{"name": "mcp__echo__echo", "arguments": {"text": "ping"}}]},
                {"content": "已经回显"},
            ]
        )
        hub.override = scripted
        async for _event in iter_events(db, conversation, "把 ping 回显出来", []):
            pass
        offered = scripted.calls[0]["tools"]
        assert "mcp__echo__echo" in offered
        assert "list_mcp_tools" not in offered
        assert "call_mcp_tool" not in offered
        prompt = json.dumps(scripted.calls[0]["messages"], ensure_ascii=False)
        assert "回显文本" in prompt
        assert "这是回显服务" in prompt
        assert "mcp__echo__echo" in prompt
        follow = json.dumps(scripted.calls[1]["messages"], ensure_ascii=False)
        assert "ping" in follow
        spec = next(item for item in schemas() if item["function"]["name"] == "mcp__echo__echo")
        assert spec["function"]["parameters"]["properties"]["text"]["type"] == "string"
        failed = await execute("mcp__echo__echo", {"text": "__error__"}, ctx)
        assert not failed.ok and "echo failed" in failed.text
        ctx.permission_mode = "plan"
        blocked = await execute("mcp__echo__echo", {"text": "ping"}, ctx)
        assert not blocked.ok and "计划模式" in blocked.text
        ctx.permission_mode = "auto"
        add_local_rule(ctx.workspace, "deny", "mcp__echo")
        hidden = [item["function"]["name"] for item in schemas(workspace=ctx.workspace)]
        assert "mcp__echo__echo" not in hidden
        denied = await execute("mcp__echo__echo", {"text": "ping"}, ctx)
        assert not denied.ok and "权限规则拒绝" in denied.text

    asyncio.run(scenario())


def test_two_mcp_servers_route_calls(client, db, ctx):
    script = str(Path(__file__).resolve().parent / "mcp_echo.py")
    created = []
    for name, label, tool in (("alpha", "alpha", "echo"), ("beta", "beta", "echo"), ("gamma", "gamma", "ping")):
        response = client.post(
            "/api/mcp",
            json={
                "name": name,
                "command": sys.executable,
                "args": [script],
                "env": {"MCP_LABEL": label, "MCP_TOOL": tool},
            },
        )
        assert response.status_code == 200
        created.append(response.json()["name"])
    assert created == ["alpha", "beta", "gamma"]
    listed = [item["name"] for item in client.get("/api/mcp").json()["items"]]
    assert listed == ["alpha", "beta", "gamma"]
    db.rollback()

    async def scenario():
        conversation = Conversation(
            title="多 MCP",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(ctx.workspace),
        )
        db.add(conversation)
        db.commit()
        scripted = ScriptedProvider(
            [
                {
                    "tool_calls": [
                        {"id": "call-alpha", "name": "mcp__alpha__echo", "arguments": {"text": "from-alpha"}},
                        {"id": "call-beta", "name": "mcp__beta__echo", "arguments": {"text": "from-beta"}},
                        {"id": "call-gamma", "name": "mcp__gamma__ping", "arguments": {"text": "from-gamma"}},
                    ]
                },
                {"content": "三个服务都调过了"},
            ]
        )
        hub.override = scripted
        async for _event in iter_events(db, conversation, "分别回显", []):
            pass
        offered = scripted.calls[0]["tools"]
        assert "mcp__alpha__echo" in offered
        assert "mcp__beta__echo" in offered
        assert "mcp__gamma__ping" in offered
        assert "list_mcp_tools" not in offered and "call_mcp_tool" not in offered
        prompt = json.dumps(scripted.calls[0]["messages"], ensure_ascii=False)
        assert "服务标记 alpha" in prompt and "服务标记 beta" in prompt and "服务标记 gamma" in prompt
        follow = {
            item.get("tool_call_id"): item.get("content")
            for item in scripted.calls[1]["messages"]
            if item.get("role") == "tool"
        }
        assert follow["call-alpha"] == "alpha/echo:from-alpha"
        assert follow["call-beta"] == "beta/echo:from-beta"
        assert follow["call-gamma"] == "gamma/ping:from-gamma"
        stored = {
            row.tool_name: row.content
            for row in db.query(Message).filter(Message.conversation_id == conversation.id, Message.role == "tool")
        }
        assert stored["mcp__alpha__echo"] == "alpha/echo:from-alpha"
        assert stored["mcp__beta__echo"] == "beta/echo:from-beta"
        assert stored["mcp__gamma__ping"] == "gamma/ping:from-gamma"

    asyncio.run(scenario())


def test_mcp_session_is_reused_and_failure_keeps_tools(ctx, db):
    script = str(Path(__file__).resolve().parent / "mcp_echo.py")

    async def scenario():
        db.add(
            McpServer(
                name="count",
                command=sys.executable,
                args=[script],
                env={"MCP_COUNT": "1"},
                enabled=True,
            )
        )
        db.commit()
        await refresh(db, force=True)
        first = await execute("mcp__count__echo", {"text": "ping"}, ctx)
        second = await execute("mcp__count__echo", {"text": "ping"}, ctx)
        assert first.ok and first.text == "ping#1"
        assert second.ok and second.text == "ping#2"
        original = mcp_client.connect

        async def fail(*_args, **_kwargs):
            raise RuntimeError("down")

        mcp_client.connect = fail
        try:
            await refresh(db, force=True)
        finally:
            mcp_client.connect = original
        assert find_tool("mcp__count__echo") is not None
        assert "仍使用上次" in prompt_text()

    asyncio.run(scenario())


def test_builtin_mcp_servers_and_delete(client, ctx, db):
    import hashlib

    from mcp_servers.catalog import builtin_servers

    for item in builtin_servers():
        db.add(
            McpServer(
                name=item["name"],
                command=item["command"],
                args=item["args"],
                env=item["env"],
                enabled=True,
            )
        )
    db.commit()

    async def scenario():
        await refresh(db, force=True)
        names = [item["function"]["name"] for item in schemas()]
        for expected in (
            "mcp__time__now",
            "mcp__time__convert_time",
            "mcp__calc__calculate",
            "mcp__text__base64_encode",
            "mcp__text__sha256",
            "mcp__units__convert_unit",
        ):
            assert expected in names
        calc = await execute("mcp__calc__calculate", {"expression": "(2+3)*4"}, ctx)
        assert calc.ok and calc.text == "20"
        units = await execute("mcp__units__convert_unit", {"value": 2, "from_unit": "km", "to_unit": "m"}, ctx)
        assert units.ok and units.text == "2000"
        temp = await execute("mcp__units__convert_unit", {"value": 0, "from_unit": "C", "to_unit": "F"}, ctx)
        assert temp.ok and temp.text == "32"
        encoded = await execute("mcp__text__base64_encode", {"text": "ping"}, ctx)
        assert encoded.ok and encoded.text == "cGluZw=="
        digest = await execute("mcp__text__sha256", {"text": "ping"}, ctx)
        assert digest.ok and digest.text == hashlib.sha256(b"ping").hexdigest()
        clock = await execute(
            "mcp__time__convert_time",
            {"time": "2026-01-01T00:00:00", "from_timezone": "UTC", "to_timezone": "Asia/Shanghai"},
            ctx,
        )
        assert clock.ok and "08:00:00" in clock.text
        rejected = await execute("mcp__calc__calculate", {"expression": "__import__('os')"}, ctx)
        assert not rejected.ok

    asyncio.run(scenario())
    created = client.post("/api/mcp", json={"name": "temp", "command": sys.executable, "args": ["missing.py"]}).json()
    assert client.delete(f"/api/mcp/{created['id']}").status_code == 200
    assert all(item["id"] != created["id"] for item in client.get("/api/mcp").json()["items"])
    assert client.delete(f"/api/mcp/{created['id']}").status_code == 404


def test_memory_task_skill_api(client):
    assert client.post("/api/memories", json={"content": "记住接口"}).status_code == 200
    assert client.get("/api/memories").json()["items"]
    task = client.post("/api/tasks", json={"title": "接口任务", "status": "pending"}).json()
    assert task["id"]
    skill = client.post("/api/skills", json={"name": "local", "description": "本地", "body": "按步骤来"}).json()
    assert skill["name"] == "local"
    names = [item["name"] for item in client.get("/api/skills").json()["items"]]
    assert "review" in names and "local" in names


def test_provider_request_shape():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((str(request.url), json.loads(request.content.decode())))
        chunk = {"choices": [{"index": 0, "delta": {"content": "pong"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}
        body = "data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n"
        return httpx.Response(200, text=body)

    transport = httpx.MockTransport(handler)

    async def scenario():
        deepseek = OpenAICompatProvider("https://api.deepseek.com", "test-key", "deepseek", transport=transport)
        text = await collect_text(
            deepseek,
            "deepseek-chat",
            "ping",
            tools=[{"type": "function", "function": {"name": "system_info", "parameters": {"type": "object"}}}],
            max_tokens=32,
        )
        assert "pong" in text
        ollama = OpenAICompatProvider("http://127.0.0.1:11434/v1", "ollama", "ollama", transport=transport)
        await collect_text(ollama, "qwen3:4b", "ping", tools=None, max_tokens=16)

    asyncio.run(scenario())
    assert captured[0][0] == "https://api.deepseek.com/chat/completions"
    assert captured[0][1]["model"] == "deepseek-chat"
    assert captured[0][1]["tools"][0]["function"]["name"] == "system_info"
    assert captured[0][1]["stream"] is True
    assert captured[1][0] == "http://127.0.0.1:11434/v1/chat/completions"
    assert captured[1][1]["model"] == "qwen3:4b"
    assert "tools" not in captured[1][1]


def test_provider_keeps_tools_and_caps_overload(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr("app.agent.providers.asyncio.sleep", no_sleep)
    calls = []

    def overloaded(_request):
        calls.append("529")
        return httpx.Response(529, text='{"error":{"type":"overloaded_error"}}')

    async def scenario():
        provider = OpenAICompatProvider("https://api.deepseek.com", "test-key", "deepseek", transport=httpx.MockTransport(overloaded))
        with pytest.raises(RuntimeError, match="没有备用模型"):
            async for _event in provider.stream([{"role": "user", "content": "hi"}], [{"type": "function"}], "deepseek-chat"):
                pass
        assert len(calls) == 3
        calls.clear()
        with pytest.raises(ModelFallback) as raised:
            async for _event in provider.stream(
                [{"role": "user", "content": "hi"}],
                [{"type": "function"}],
                "deepseek-chat",
                fallback_model="deepseek-reasoner",
            ):
                pass
        assert raised.value.fallback == "deepseek-reasoner"
        assert len(calls) == 3
        calls.clear()
        with pytest.raises(RuntimeError, match="模型过载"):
            await collect_text(provider, "deepseek-chat", "hi")
        assert len(calls) == 1

    asyncio.run(scenario())

    bad = []

    def rejected(request):
        bad.append(json.loads(request.content.decode()))
        return httpx.Response(400, text="bad schema")

    async def once():
        provider = OpenAICompatProvider("https://api.deepseek.com", "test-key", "deepseek", transport=httpx.MockTransport(rejected))
        with pytest.raises(RuntimeError, match="400"):
            async for _event in provider.stream(
                [{"role": "user", "content": "hi"}],
                [{"type": "function", "function": {"name": "bash"}}],
                "deepseek-chat",
            ):
                pass

    asyncio.run(once())
    assert len(bad) == 1
    assert bad[0]["tools"][0]["function"]["name"] == "bash"


def test_tool_fields_describe_units_and_defaults():
    listed = {item["function"]["name"]: item["function"]["parameters"] for item in schemas()}

    def walk(schema):
        for field in (schema.get("properties") or {}).values():
            assert field.get("description")
            if field.get("type") == "array" and isinstance(field.get("items"), dict):
                walk(field["items"])
            elif field.get("type") == "object":
                walk(field)

    for name, schema in listed.items():
        if name.startswith("mcp__"):
            continue
        walk(schema)
    assert "秒" in listed["bash"]["properties"]["timeout"]["description"]
    assert listed["grep"]["properties"]["output_mode"]["enum"][0] == "content"
    assert "50" in listed["grep"]["properties"]["head_limit"]["description"]
    for flag in ("-A", "-B", "-C", "-i"):
        assert flag in listed["grep"]["properties"]


def test_clawd_argument_names_reach_the_handlers(ctx):
    from app.agent.tools import normalize_arguments

    kept = normalize_arguments("edit_file", {"path": "a.txt", "file_path": "b.txt", "old": "", "old_string": "x", "new_string": ""})
    assert kept["path"] == "a.txt"
    assert kept["old"] == ""
    assert kept["new"] == ""

    async def scenario():
        written = await execute("write_file", {"file_path": "alias.txt", "content": "ok"}, ctx)
        assert written.ok and (ctx.workspace / "alias.txt").read_text(encoding="utf-8") == "ok"
        edited = await execute("edit_file", {"file_path": "alias.txt", "old_string": "ok", "new_string": "yes"}, ctx)
        assert edited.ok and (ctx.workspace / "alias.txt").read_text(encoding="utf-8") == "yes"
        missing = await execute("use_skill", {"skill": "没有这个技能"}, ctx)
        assert "缺少必填参数 `name`" not in missing.text
        stopped = await execute("task_stop", {"task_id": "no-such-job"}, ctx)
        assert "缺少必填参数 `id`" not in stopped.text
        assert "没有这个后台任务" in stopped.text

    asyncio.run(scenario())


def test_argument_errors_and_dont_ask(ctx):
    from app.agent.tools import argument_issues, execute

    text = argument_issues("remember", {"type": "object", "properties": {"content": {"type": "string"}, "line": {"type": "integer"}}, "required": ["content"]}, {"line": "1", "extra": True})
    assert "缺少必填参数 `content`" in text
    assert "应为 integer" in text
    assert "extra" not in text

    async def scenario():
        missing = await execute("remember", {}, ctx)
        assert not missing.ok and "content" in missing.text
        ctx.permission_mode = "dontAsk"
        denied = await execute("write_file", {"path": "note.txt", "content": "x"}, ctx)
        assert not denied.ok and "不询问" in denied.text
        ctx.permission_mode = "acceptEdits"
        allowed = await execute("write_file", {"path": "note.txt", "content": "x"}, ctx)
        assert allowed.ok

    asyncio.run(scenario())


def test_mode_aliases(client):
    saved = client.put("/api/settings", json={"permission_mode": "acceptEdits", "fallback_model": "deepseek-reasoner"}).json()
    assert saved["values"]["permission_mode"] == "edits"
    assert saved["values"]["fallback_model"] == "deepseek-reasoner"
    created = client.post("/api/conversations", json={"title": "模式"}).json()
    patched = client.patch(f"/api/conversations/{created['id']}", json={"permission_mode": "dontAsk"}).json()
    assert patched["permission_mode"] == "dontAsk"


def test_interrupted_tool_still_returns_a_result():
    from app.agent.executor import StreamingToolExecutor

    executor = StreamingToolExecutor(object())
    executor.items.append({"id": "call-1", "name": "bash", "arguments": {}})

    async def scenario():
        results = await executor.finish()
        assert results[0].text == "这次请求被中断了"
        assert results[0].ok is False

    asyncio.run(scenario())


def test_unknown_approval(client):
    response = client.post("/api/approvals/missing", json={"approved": True})
    assert response.status_code == 404


@pytest.mark.parametrize(
    "command",
    ["/help", "/cost", "/tasks", "/memory 喜欢短回答"],
)
def test_slash_commands(client, command):
    created = client.post("/api/conversations", json={"title": "斜杠"}).json()
    with client.stream("POST", f"/api/conversations/{created['id']}/chat", json={"content": command}) as response:
        body = "".join(response.iter_text())
    assert '"type": "done"' in body or '"type":"done"' in body
    assert "error" not in body.lower() or "未通过" in body
