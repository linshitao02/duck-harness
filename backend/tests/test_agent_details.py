import asyncio
import json
from dataclasses import replace
from pathlib import Path

from app.agent.loop import iter_events
from app.agent.providers import ScriptedProvider, hub
from app.agent.tools import broker, execute
from app.models import Conversation, Message


def test_edit_requires_fresh_read_and_accepts_quotes(ctx):
    async def scenario():
        written = await execute("write_file", {"path": "quote.txt", "content": "他说“你好”\n"}, ctx)
        assert written.ok
        edited = await execute("edit_file", {"path": "quote.txt", "old": '他说"你好"', "new": "他说完了"}, ctx)
        assert edited.ok
        assert "他说完了" in (ctx.workspace / "quote.txt").read_text(encoding="utf-8")

        lines = "\n".join(f"line-{index}" for index in range(10)) + "\n"
        await execute("write_file", {"path": "long.txt", "content": lines}, ctx)
        partial = await execute("read_file", {"path": "long.txt", "limit": 2}, ctx)
        assert partial.ok
        blocked = await execute("edit_file", {"path": "long.txt", "old": "line-3", "new": "line-x"}, ctx)
        assert not blocked.ok
        full = await execute("read_file", {"path": "long.txt"}, ctx)
        assert "1|line-0" in full.text
        fixed = await execute("edit_file", {"path": "long.txt", "old": "1|line-3", "new": "line-x"}, ctx)
        assert fixed.ok

    asyncio.run(scenario())


def test_plan_mode_readonly_bash_and_plan_file(ctx, db):
    async def scenario():
        conversation = Conversation(
            title="计划",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(ctx.workspace),
        )
        db.add(conversation)
        db.commit()
        local = replace(ctx, conversation_id=conversation.id)
        assert (await execute("enter_plan_mode", {}, local)).ok
        assert local.permission_mode == "plan"
        listed = await execute("bash", {"command": "Write-Output plan-ok"}, local)
        assert listed.ok and "plan-ok" in listed.text
        denied = await execute("bash", {"command": "New-Item nope.txt -ItemType File"}, local)
        assert not denied.ok
        planned = await execute("write_file", {"path": ".harness/plan.md", "content": "先看再改"}, local)
        assert planned.ok
        blocked = await execute("write_file", {"path": "app.txt", "content": "x"}, local)
        assert not blocked.ok

        async def allow():
            while not broker.pending:
                await asyncio.sleep(0.01)
            broker.resolve(next(iter(broker.pending)), True)

        waiter = asyncio.create_task(allow())
        exited = await execute("exit_plan_mode", {}, local)
        await waiter
        assert exited.ok
        assert local.permission_mode == "auto"

    asyncio.run(scenario())


def test_ask_user_todo_notebook_and_grep(ctx, db):
    async def scenario():
        conversation = Conversation(
            title="细节",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(ctx.workspace),
        )
        db.add(conversation)
        db.commit()
        local = replace(ctx, conversation_id=conversation.id)

        async def answer():
            while not broker.pending:
                await asyncio.sleep(0.01)
            broker.resolve(next(iter(broker.pending)), True, {"用哪种？": "甲"})

        waiter = asyncio.create_task(answer())
        asked = await execute(
            "ask_user",
            {
                "questions": [
                    {
                        "question": "用哪种？",
                        "options": [{"label": "甲", "description": "第一种"}, {"label": "乙"}],
                    }
                ]
            },
            local,
        )
        await waiter
        assert asked.ok and "甲" in asked.text

        todos = await execute(
            "todo_write",
            {
                "todos": [
                    {"content": "读代码", "status": "in_progress"},
                    {"content": "改代码", "status": "pending"},
                ]
            },
            local,
        )
        assert todos.ok and "读代码" in todos.text
        clash = await execute(
            "todo_write",
            {"merge": True, "todos": [{"content": "再来一件", "status": "in_progress"}]},
            local,
        )
        assert not clash.ok

        notebook = {
            "cells": [{"cell_type": "code", "source": ["print('cell-one')\n"]}],
            "nbformat": 4,
            "nbformat_minor": 5,
        }
        (ctx.workspace / "demo.ipynb").write_text(json.dumps(notebook), encoding="utf-8")
        assert (await execute("read_file", {"path": "demo.ipynb"}, local)).ok
        changed = await execute(
            "notebook_edit",
            {
                "notebook_path": "demo.ipynb",
                "cell_id": "0",
                "new_source": "print('cell-two')",
                "edit_mode": "replace",
            },
            local,
        )
        assert changed.ok
        assert "cell-two" in (ctx.workspace / "demo.ipynb").read_text(encoding="utf-8")
        rejected = await execute("edit_file", {"path": "demo.ipynb", "old": "cell-two", "new": "x"}, local)
        assert not rejected.ok

        (ctx.workspace / "keep.py").write_text("print('hello-marker')\n", encoding="utf-8")
        (ctx.workspace / "skip.txt").write_text("hello-marker\n", encoding="utf-8")
        found = await execute(
            "grep",
            {"pattern": "hello-marker", "glob": "*.py", "output_mode": "files_with_matches"},
            local,
        )
        assert "keep.py" in found.text
        assert "skip.txt" not in found.text

    asyncio.run(scenario())


def test_background_hook_skill_and_worktree(ctx):
    async def scenario():
        started = await execute("bash", {"command": "Write-Output bg-ok", "run_in_background": True}, ctx)
        assert started.ok
        job_id = started.text.split("#", 1)[1].strip()
        seen = ""
        for _ in range(30):
            output = await execute("task_output", {"id": job_id}, ctx)
            seen = output.text
            if "bg-ok" in seen:
                break
            await asyncio.sleep(0.1)
        assert "bg-ok" in seen

        hook = ctx.workspace / ".harness"
        hook.mkdir(parents=True)
        (hook / "hooks.json").write_text(
            json.dumps({"pre_tool": [{"tools": ["write_file"], "command": "exit 1"}]}),
            encoding="utf-8",
        )
        blocked = await execute("write_file", {"path": "hooked.txt", "content": "no"}, ctx)
        assert not blocked.ok
        assert not (ctx.workspace / "hooked.txt").exists()
        (hook / "hooks.json").unlink()

        skill = ctx.workspace / ".harness" / "skills" / "local-skill"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: local-skill\ndescription: 本地技能\n---\n按这个做\n", encoding="utf-8")
        loaded = await execute("use_skill", {"name": "local-skill"}, ctx)
        assert loaded.ok and "按这个做" in loaded.text

        missing = await execute("enter_worktree", {"name": "trial"}, ctx)
        assert not missing.ok

    asyncio.run(scenario())


def test_truncated_output_continues(db, tmp_path):
    async def scenario():
        conversation = Conversation(
            title="续写",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(tmp_path),
        )
        db.add(conversation)
        db.commit()
        hub.override = ScriptedProvider(
            [{"content": "前半段", "finish": "length"}, {"content": "后半段"}]
        )
        collected = []
        async for event in iter_events(db, conversation, "写长一点", []):
            collected.append(event)
        assert any(event["type"] == "done" and event["content"] == "后半段" for event in collected)
        bodies = [row.content for row in db.query(Message).filter(Message.conversation_id == conversation.id)]
        assert "前半段" in bodies and "后半段" in bodies

    asyncio.run(scenario())


def test_tool_batch_resets_length_recoveries(db, tmp_path):
    async def scenario():
        conversation = Conversation(
            title="恢复次数",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(tmp_path),
        )
        db.add(conversation)
        db.commit()
        scripted = ScriptedProvider(
            [
                {"content": "甲", "finish": "length"},
                {"content": "乙", "finish": "length"},
                {"content": "丙", "finish": "length"},
                {"tool_calls": [{"name": "system_info", "arguments": {}}]},
                {"content": "丁", "finish": "length"},
                {"content": "收尾"},
            ]
        )
        hub.override = scripted
        async for _event in iter_events(db, conversation, "分几段写", []):
            pass
        bodies = [row.content for row in db.query(Message).filter(Message.conversation_id == conversation.id)]
        assert "收尾" in bodies
        assert scripted.index == 6

    asyncio.run(scenario())


def test_reasoner_sends_thinking_back_with_tool_calls(db, tmp_path):
    from app.agent.loop import build_model_messages

    async def scenario():
        conversation = Conversation(
            title="思考",
            provider="deepseek",
            model="deepseek-reasoner",
            permission_mode="auto",
            workspace=str(tmp_path),
        )
        db.add(conversation)
        db.commit()
        scripted = ScriptedProvider(
            [
                {"reasoning": "先看本机情况", "tool_calls": [{"name": "system_info", "arguments": {}}]},
                {"reasoning": "已经看完", "content": "本机正常"},
            ]
        )
        hub.override = scripted
        async for _event in iter_events(db, conversation, "看一下", []):
            pass
        follow = scripted.calls[1]["messages"]
        thought = next(item for item in follow if item.get("role") == "assistant" and item.get("tool_calls"))
        assert thought["reasoning_content"] == "先看本机情况"
        assert thought.get("content") != "先看本机情况"
        rows = db.query(Message).filter(Message.conversation_id == conversation.id, Message.role == "assistant").all()
        assert rows[0].reasoning == "先看本机情况"
        assert rows[0].content != "先看本机情况"
        assert rows[-1].content == "本机正常"
        rebuilt = build_model_messages(db, conversation, tmp_path)
        again = next(item for item in rebuilt if item.get("role") == "assistant" and item.get("tool_calls"))
        assert again["reasoning_content"] == "先看本机情况"

        conversation.model = "deepseek-chat"
        db.commit()
        plain = ScriptedProvider(
            [
                {"reasoning": "不要给用户看", "tool_calls": [{"name": "system_info", "arguments": {}}]},
                {"content": "普通回答"},
            ]
        )
        hub.override = plain
        async for _event in iter_events(db, conversation, "再看一下", []):
            pass
        chat_follow = plain.calls[1]["messages"]
        assert all("reasoning_content" not in item for item in chat_follow)
        chat_thought = [item for item in chat_follow if item.get("role") == "assistant" and item.get("tool_calls")][-1]
        assert chat_thought.get("content") != "不要给用户看"

    asyncio.run(scenario())


def test_project_instructions_enter_prompt(db, tmp_path):
    async def scenario():
        (tmp_path / "HARNESS.md").write_text("项目约定：先写测试。", encoding="utf-8")
        conversation = Conversation(
            title="说明",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(tmp_path),
        )
        db.add(conversation)
        db.commit()
        scripted = ScriptedProvider([{"content": "收到约定"}])
        hub.override = scripted
        async for _event in iter_events(db, conversation, "看说明", []):
            pass
        blob = json.dumps(scripted.calls[0]["messages"], ensure_ascii=False)
        assert "先写测试" in blob
        assert "explore" in blob

    asyncio.run(scenario())


def test_tool_batch_resets_context_recoveries(db, tmp_path):
    from app.agent.providers import StreamEvent

    class Planned:
        def __init__(self):
            self.plan = ["overflow", "overflow", "tool", "overflow", "完成"]
            self.index = 0

        async def stream(self, messages, tools, model, should_stop=None, max_tokens=None, query_source="foreground", fallback_model=""):
            del messages, tools, model, should_stop, max_tokens, fallback_model
            if query_source != "foreground":
                yield StreamEvent(kind="text", text="旁路摘要")
                yield StreamEvent(kind="usage", prompt_tokens=1, completion_tokens=1)
                return
            kind = self.plan[self.index]
            self.index += 1
            if kind == "overflow":
                raise RuntimeError("maximum context length exceeded")
            if kind == "tool":
                yield StreamEvent(kind="tool_call", tool_id="c1", tool_name="system_info", arguments_delta="{}")
            else:
                yield StreamEvent(kind="text", text=kind)
            yield StreamEvent(kind="usage", prompt_tokens=1, completion_tokens=1)

    async def scenario():
        conversation = Conversation(
            title="压缩次数",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(tmp_path),
        )
        db.add(conversation)
        db.commit()
        provider = Planned()
        hub.override = provider
        collected = []
        async for event in iter_events(db, conversation, "继续", []):
            collected.append(event)
        bodies = [row.content for row in db.query(Message).filter(Message.conversation_id == conversation.id)]
        assert "完成" in bodies
        assert provider.index == 5
        assert not any(event["type"] == "error" for event in collected)

    asyncio.run(scenario())


def test_subagent_recovers_length_without_spending_a_step(ctx):
    from app.agent.loop import run_loop

    async def scenario():
        provider = ScriptedProvider(
            [{"content": "前半", "finish": "length"}, {"content": "后半"}]
        )
        text = await run_loop([{"role": "user", "content": "写"}], provider, ctx, max_steps=1)
        return text, provider.index

    text, index = asyncio.run(scenario())
    assert text == "后半"
    assert index == 2


def test_subagent_tool_batch_resets_length_recoveries(ctx):
    from app.agent.loop import run_loop

    async def scenario():
        provider = ScriptedProvider(
            [
                {"content": "甲", "finish": "length"},
                {"content": "乙", "finish": "length"},
                {"content": "丙", "finish": "length"},
                {"tool_calls": [{"name": "system_info", "arguments": {}}]},
                {"content": "丁", "finish": "length"},
                {"content": "收尾"},
            ]
        )
        text = await run_loop([{"role": "user", "content": "分几段"}], provider, ctx, max_steps=2)
        return text, provider.index

    text, index = asyncio.run(scenario())
    assert text == "收尾"
    assert index == 6


def test_subagent_stop_hook_is_not_capped_at_five(ctx):
    from app.agent.loop import run_loop

    async def scenario():
        folder = ctx.workspace / ".harness"
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
        provider = ScriptedProvider([{"content": "草稿"}, {"content": "定稿"}])
        text = await run_loop([{"role": "user", "content": "写一版"}], provider, ctx, max_steps=1)
        count = (folder / "stop-count.txt").read_text(encoding="ascii").strip()
        return text, count, provider.index

    text, count, index = asyncio.run(asyncio.wait_for(scenario(), timeout=40))
    assert text == "定稿"
    assert count == "7"
    assert index == 7


def test_subagent_compacts_long_transcript(ctx):
    from app.agent.loop import run_loop

    async def scenario():
        messages = [{"role": "system", "content": "系统"}]
        for index in range(6):
            messages.append({"role": "user", "content": "早期块" + ("早期" * 8000)})
            messages.append({"role": "assistant", "content": f"旧回答{index}"})
        messages.extend(
            [
                {"role": "user", "content": "倒数第二问"},
                {"role": "assistant", "content": "倒数第二答"},
                {"role": "user", "content": "最近的问题"},
                {"role": "assistant", "content": "最近的回答"},
            ]
        )
        provider = ScriptedProvider(
            [{"content": "摘要保留标记 MARK-OLD"}, {"content": "子代理结论"}]
        )
        text = await run_loop(messages, provider, ctx, max_steps=2)
        return text, provider.calls

    text, calls = asyncio.run(scenario())
    assert text == "子代理结论"
    first = json.dumps(calls[0]["messages"], ensure_ascii=False)
    follow = json.dumps(calls[1]["messages"], ensure_ascii=False)
    assert "早期块" in first
    assert "MARK-OLD" in follow
    assert "最近的回答" in follow
    assert "早期块" not in follow


def test_subagent_compacts_again_after_a_tool_batch(ctx):
    from app.agent.loop import run_loop
    from app.agent.providers import StreamEvent

    class Planned:
        def __init__(self):
            self.plan = ["overflow", "overflow", "tool", "overflow", "完成"]
            self.index = 0
            self.compacts = 0

        async def stream(self, messages, tools, model, should_stop=None, max_tokens=None, query_source="foreground", fallback_model=""):
            del messages, tools, model, should_stop, max_tokens, fallback_model
            if query_source == "compact":
                self.compacts += 1
                yield StreamEvent(kind="text", text="压缩摘要")
                yield StreamEvent(kind="usage", prompt_tokens=1, completion_tokens=1)
                return
            kind = self.plan[self.index]
            self.index += 1
            if kind == "overflow":
                raise RuntimeError("maximum context length exceeded")
            if kind == "tool":
                yield StreamEvent(kind="tool_call", tool_id="c1", tool_name="system_info", arguments_delta="{}")
            else:
                yield StreamEvent(kind="text", text=kind)
            yield StreamEvent(kind="usage", prompt_tokens=1, completion_tokens=1)

    async def scenario():
        messages = [{"role": "system", "content": "系统"}]
        for index in range(4):
            messages.append({"role": "user", "content": f"旧事实-标记 {index}"})
            messages.append({"role": "assistant", "content": f"旧答复 {index}"})
        messages.append({"role": "user", "content": "最近的问题"})
        provider = Planned()
        text = await run_loop(messages, provider, ctx, max_steps=2)
        return text, provider.index, provider.compacts

    text, index, compacts = asyncio.run(scenario())
    assert text == "完成"
    assert index == 5
    assert compacts >= 2
