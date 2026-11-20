import asyncio
import json
import re

from app.agent.history import rewind
from app.agent.loop import iter_events
from app.agent.providers import ScriptedProvider, hub
from app.agent.shrink import CLEARED, shrink_tool_results
from app.agent.tools import execute
from app.models import Conversation, Message


def test_permission_rules_deny_and_allow(ctx):
    async def scenario():
        local = ctx.workspace / ".harness"
        local.mkdir(parents=True)
        (local / "settings.local.json").write_text(
            json.dumps(
                {
                    "permissions": {
                        "deny": ["write_file"],
                        "allow": ["bash(Write-Output*)"],
                    }
                }
            ),
            encoding="utf-8",
        )
        denied = await execute("write_file", {"path": "secret.txt", "content": "no"}, ctx)
        assert not denied.ok
        assert "权限规则拒绝了" in denied.text
        assert not (ctx.workspace / "secret.txt").exists()

        ctx.permission_mode = "ask"
        allowed = await execute("bash", {"command": "Write-Output rule-ok"}, ctx)
        assert allowed.ok and "rule-ok" in allowed.text

    asyncio.run(scenario())


def test_post_hook_diff_and_rewind(ctx):
    async def scenario():
        hook = ctx.workspace / ".harness"
        hook.mkdir(parents=True)
        (hook / "hooks.json").write_text(
            json.dumps({"post_tool": [{"tools": ["write_file"], "command": "Write-Output HOOK-NOTE"}]}),
            encoding="utf-8",
        )
        written = await execute("write_file", {"path": "note.txt", "content": "one\n"}, ctx)
        assert written.ok and "HOOK-NOTE" in written.text
        assert written.diff
        await execute("read_file", {"path": "note.txt"}, ctx)
        edited = await execute("edit_file", {"path": "note.txt", "old": "one", "new": "two"}, ctx)
        assert edited.ok
        assert "+two" in edited.diff
        restored = rewind(ctx.workspace, None, 1)
        assert restored
        assert (ctx.workspace / "note.txt").read_text(encoding="utf-8") == "one\n"
        removed = rewind(ctx.workspace, None, 1)
        assert removed
        assert not (ctx.workspace / "note.txt").exists()

    asyncio.run(scenario())


def test_custom_agent_and_resume(ctx):
    async def scenario():
        folder = ctx.workspace / ".harness" / "agents"
        folder.mkdir(parents=True)
        (folder / "reviewer.md").write_text(
            "---\nname: reviewer\ndescription: 审查代码\ntools: read_file, grep\npermissionMode: plan\n---\n只看不改。\n",
            encoding="utf-8",
        )
        hub.override = ScriptedProvider([{"content": "第一轮"}, {"content": "第二轮"}])
        first = await execute("spawn_agent", {"agent": "reviewer", "task": "看一下"}, ctx)
        assert first.ok and "第一轮" in first.text
        names = hub.override.calls[0]["tools"]
        assert "read_file" in names and "grep" in names and "write_file" not in names
        assert "只看不改" in hub.override.calls[0]["messages"][0]["content"]
        match = re.search(r"子代理 ([0-9a-f]+)", first.text)
        assert match
        second = await execute("spawn_agent", {"resume": match.group(1), "task": "继续"}, ctx)
        assert second.ok and "第二轮" in second.text
        assert "继续" in [item.get("content") for item in hub.override.calls[1]["messages"]]

    asyncio.run(scenario())


def test_shrink_keeps_recent_tool_results():
    messages = []
    for index in range(9):
        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": f"c{index}", "function": {"name": "read_file", "arguments": "{}"}}],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": "x" * 50})
    shrink_tool_results(messages)
    contents = [item["content"] for item in messages if item["role"] == "tool"]
    assert contents[:5] == [CLEARED] * 5
    assert contents[-1] == "x" * 50


def test_stop_hook_continues_once(db, tmp_path):
    async def scenario():
        folder = tmp_path / ".harness"
        folder.mkdir()
        command = (
            "$p = Join-Path (Get-Location) '.harness\\stop-count.txt'; "
            "$n = 0; "
            "if (Test-Path -LiteralPath $p) { $n = [int](Get-Content -LiteralPath $p | Select-Object -First 1) }; "
            "$n = $n + 1; "
            "Set-Content -LiteralPath $p -Value $n -Encoding ascii; "
            "if ($n -le 6) { Write-Output 'NEED-FIX'; exit 2 }; "
            "exit 0"
        )
        (folder / "hooks.json").write_text(
            json.dumps({"stop": [{"command": command}]}),
            encoding="utf-8",
        )
        conversation = Conversation(
            title="停止钩子",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(tmp_path),
        )
        db.add(conversation)
        db.commit()
        scripted = ScriptedProvider([{"content": "草稿"}, {"content": "定稿"}])
        hub.override = scripted
        collected = []
        async for event in iter_events(db, conversation, "写一版", []):
            collected.append(event)
        assert any(event["type"] == "done" and event["content"] == "定稿" for event in collected)
        bodies = [row.content for row in db.query(Message).filter(Message.conversation_id == conversation.id)]
        assert any("NEED-FIX" in body for body in bodies)
        assert "定稿" in bodies
        assert (folder / "stop-count.txt").read_text(encoding="ascii").strip() == "7"
        assert scripted.index == 7

    asyncio.run(asyncio.wait_for(scenario(), timeout=40))
