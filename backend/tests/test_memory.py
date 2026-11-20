"""长期记忆的索引、类型和更新，以及会话笔记和压缩时保留上一份摘要。"""

import json
import os
from datetime import datetime, timedelta

from app.agent.memory import (
    SESSION_INIT_CHARS,
    forget_memory,
    memory_prompt,
    read_memory,
    rebuild_notes,
    save_memory,
    should_extract,
    truncate_entrypoint,
)
from app.agent.providers import ScriptedProvider, hub
from app.agent.tools import execute
from app.models import Conversation, Message
from app.services.runtime import compact_conversation


def test_memory_index_updates_and_hides_the_body(ctx):
    async def scenario():
        saved = await execute(
            "remember",
            {
                "name": "测试约定",
                "type": "feedback",
                "description": "集成测试要打真库",
                "content": "集成测试必须打真库。\n\n**Why:** 假库放过了迁移。\n**How to apply:** 这个仓库的测试不要 mock 数据库。\n" + ("细节" * 80),
            },
            ctx,
        )
        body = await execute("read_memory", {"name": "测试约定"}, ctx)
        again = await execute(
            "remember",
            {
                "name": "测试约定",
                "type": "feedback",
                "description": "集成测试要打真库",
                "content": "规则没变，原因补充了一句。",
            },
            ctx,
        )
        listed = await execute("list_memories", {}, ctx)
        rejected = await execute("remember", {"name": "坏类型", "type": "secret", "content": "不要"}, ctx)
        removed = await execute("forget_memory", {"name": "测试约定"}, ctx)
        gone = await execute("read_memory", {"name": "测试约定"}, ctx)
        return saved, again, listed, body, rejected, removed, gone

    import asyncio

    saved, again, listed, body, rejected, removed, gone = asyncio.run(scenario())
    folder = ctx.workspace / ".harness" / "memory"
    files = [path.name for path in folder.glob("*.md") if path.name != "MEMORY.md"]
    assert saved.ok and again.ok
    assert files == []
    assert "已忘掉" in removed.text
    assert not gone.ok
    assert not rejected.ok and "type" in rejected.text
    assert "测试约定" in listed.text and "集成测试要打真库" in listed.text
    assert "假库放过了迁移" not in listed.text
    assert "假库放过了迁移" in body.text
    assert (folder / "MEMORY.md").read_text(encoding="utf-8").strip() == ""
    prompt = memory_prompt(ctx.workspace)
    assert "不要存进记忆" in prompt and "忽略记忆" in prompt and "根据记忆提建议之前" in prompt
    assert "假库" not in prompt


def test_old_memory_warns_on_read(ctx):
    import asyncio

    save_memory(ctx.workspace, "旧决定", "当时端口是 8787", "project", "服务端口")
    path = ctx.workspace / ".harness" / "memory" / "旧决定.md"
    old = (datetime.now() - timedelta(days=3)).timestamp()
    os.utime(path, (old, old))

    async def scenario():
        recalled = await execute("read_memory", {"name": "旧决定"}, ctx)
        raw = await execute("read_file", {"path": str(path)}, ctx)
        return recalled, raw

    recalled, raw = asyncio.run(scenario())
    assert "3 天前" in recalled.text and "当时的观察" in recalled.text
    assert "3 天前" in raw.text
    assert "3 天前" in read_memory(ctx.workspace, "旧决定")


def test_index_truncates_by_line_and_bytes():
    text = "\n".join(f"- 第{index}条" for index in range(5))
    clipped = truncate_entrypoint(text, max_lines=2, max_bytes=10_000)
    assert "第0条" in clipped and "第2条" not in clipped
    assert "警告" in clipped
    wide = "字" * 50
    clipped_bytes = truncate_entrypoint(wide, max_lines=10, max_bytes=20)
    assert "警告" in clipped_bytes
    assert len(clipped_bytes.split("警告")[0]) <= 30


def test_should_extract_waits_for_growth_and_a_break():
    assert not should_extract(1000, 10, False, {})
    assert not should_extract(SESSION_INIT_CHARS, 0, False, {}, has_assistant=False)
    assert should_extract(SESSION_INIT_CHARS, 1, False, {})
    assert not should_extract(SESSION_INIT_CHARS, 1, True, {})
    state = {"initialized": True, "chars": SESSION_INIT_CHARS, "tools": 1}
    assert not should_extract(SESSION_INIT_CHARS + 1000, 4, True, state)
    assert should_extract(SESSION_INIT_CHARS + 9000, 2, False, state)
    empty = {"empty_chars": SESSION_INIT_CHARS, "empty_tools": 1}
    assert not should_extract(SESSION_INIT_CHARS, 1, False, empty)
    assert should_extract(SESSION_INIT_CHARS + 20, 1, False, empty)


def test_session_notes_keep_the_template(ctx, monkeypatch):
    import asyncio

    from app.agent.memory import maintain_session_memory

    calls = []

    async def fake_collect(provider, model, prompt, tools=None, max_tokens=128):
        calls.append(prompt)
        return "# 当前状态\n_说明不要改_\n正在改记忆模块\n\n# 关键结果\n端口保持 8787"

    monkeypatch.setattr("app.agent.providers.collect_text", fake_collect)
    messages = [
        {"role": "user", "content": "字" * SESSION_INIT_CHARS},
        {"role": "assistant", "content": "开始改记忆模块"},
    ]
    asyncio.run(maintain_session_memory(ctx.workspace, 9, messages, object(), "deepseek-chat", "上一份里有港口号 8787"))
    asyncio.run(maintain_session_memory(ctx.workspace, 9, messages, object(), "deepseek-chat", ""))

    notes = (ctx.workspace / ".harness" / "session_memory" / "9.md").read_text(encoding="utf-8")
    assert notes.count("# 当前状态") == 1
    assert "_现在正在做什么" in notes
    assert "正在改记忆模块" in notes
    assert "8787" in notes
    assert len(calls) == 1
    assert "上一份里有港口号 8787" in calls[0]
    assert "开始改记忆模块" in calls[0]


def test_notes_wait_for_an_answer_and_ignore_an_empty_draft(ctx, monkeypatch):
    import asyncio

    from app.agent.memory import maintain_session_memory

    calls = []

    async def empty_collect(provider, model, prompt, tools=None, max_tokens=128):
        calls.append(prompt)
        return "# 当前状态\n_斜体说明_\n"

    monkeypatch.setattr("app.agent.providers.collect_text", empty_collect)
    user_only = [{"role": "user", "content": "字" * SESSION_INIT_CHARS}]
    asyncio.run(maintain_session_memory(ctx.workspace, 11, user_only, object(), "deepseek-chat", ""))
    assert calls == []

    answered = user_only + [{"role": "assistant", "content": "先记下空模板"}]
    asyncio.run(maintain_session_memory(ctx.workspace, 11, answered, object(), "deepseek-chat", ""))
    asyncio.run(maintain_session_memory(ctx.workspace, 11, answered, object(), "deepseek-chat", ""))
    assert len(calls) == 1
    state_path = ctx.workspace / ".harness" / "session_memory" / "11.state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert not state.get("initialized")
    notes_path = ctx.workspace / ".harness" / "session_memory" / "11.md"
    assert not notes_path.exists()

    async def filled_collect(provider, model, prompt, tools=None, max_tokens=128):
        calls.append(prompt)
        return "# 当前状态\n_斜体说明_\n口令已经写入"

    monkeypatch.setattr("app.agent.providers.collect_text", filled_collect)
    later = answered + [
        {"role": "user", "content": "再确认一次"},
        {"role": "assistant", "content": "口令已经写入"},
    ]
    asyncio.run(maintain_session_memory(ctx.workspace, 11, later, object(), "deepseek-chat", ""))
    notes = notes_path.read_text(encoding="utf-8")
    assert "口令已经写入" in notes
    assert "_现在正在做什么" in notes
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["initialized"] is True


def test_turn_writes_notes_after_the_answer(db, tmp_path, monkeypatch):
    import asyncio

    from app.agent.loop import iter_events

    seen = []

    async def fake_collect(provider, model, prompt, tools=None, max_tokens=128):
        seen.append(prompt)
        return "# 当前状态\n_斜体说明_\n最终结论已经写进笔记"

    monkeypatch.setattr("app.agent.providers.collect_text", fake_collect)
    conversation = Conversation(
        title="笔记时机",
        provider="deepseek",
        model="deepseek-chat",
        permission_mode="auto",
        workspace=str(tmp_path),
    )
    db.add(conversation)
    db.commit()
    hub.override = ScriptedProvider([{"content": "最终结论是端口 8787"}])

    async def scenario():
        async for _event in iter_events(db, conversation, "字" * SESSION_INIT_CHARS, []):
            pass

    asyncio.run(scenario())
    assert len(seen) == 1
    assert "最终结论是端口 8787" in seen[0]
    notes = (tmp_path / ".harness" / "session_memory" / f"{conversation.id}.md").read_text(encoding="utf-8")
    assert "最终结论已经写进笔记" in notes
    assert "_现在正在做什么" in notes


def test_compact_keeps_the_previous_summary(db, tmp_path):
    import asyncio

    conversation = Conversation(
        title="压缩",
        provider="deepseek",
        model="deepseek-chat",
        permission_mode="auto",
        workspace=str(tmp_path),
        summary="港口号 8787 仍然有效",
    )
    db.add(conversation)
    db.commit()
    for index in range(6):
        db.add(
            Message(
                conversation_id=conversation.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"历史{index}",
                status="done",
            )
        )
    db.commit()
    notes = tmp_path / ".harness" / "session_memory" / f"{conversation.id}.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text(rebuild_notes("# 当前状态\n正在核对端口", ""), encoding="utf-8")
    provider = ScriptedProvider([{"content": "合并后的摘要，保留港口号 8787"}])
    hub.override = provider

    summary = asyncio.run(compact_conversation(db, conversation, hub))
    sent = json.dumps(provider.calls[0]["messages"], ensure_ascii=False)
    assert "上一份摘要" in sent and "港口号 8787" in sent and "正在核对端口" in sent
    assert "当前正在做的事" in sent and "下一步" in sent
    assert summary == "合并后的摘要，保留港口号 8787"
    db.refresh(conversation)
    assert conversation.summary == summary


def test_prompt_has_memory_rules_without_dumping_bodies(ctx):
    save_memory(ctx.workspace, "语言", "正文里有一段不该进提示词的长说明" + "甲" * 40, "user", "用中文")
    prompt = memory_prompt(ctx.workspace)
    assert "用中文" in prompt
    assert "不该进提示词" not in prompt
    assert "task_write" in prompt
    forget_memory(ctx.workspace, "语言")


def test_compact_summary_keeps_only_the_summary_block():
    from app.agent.memory import format_compact_summary

    raw = "<analysis>先读文件</analysis>\n<summary>\n8. 当前正在做的事：已经写入 note.txt\n9. 下一步：停\n</summary>"
    text = format_compact_summary(raw)
    assert "已经写入 note.txt" in text
    assert "先读文件" not in text
    assert format_compact_summary("合并后的摘要") == "合并后的摘要"


def test_unchanged_read_is_a_stub_until_the_file_changes(ctx):
    import asyncio

    from app.agent.edits import FILE_UNCHANGED

    async def scenario():
        (ctx.workspace / "note.txt").write_text("alpha\n", encoding="utf-8")
        first = await execute("read_file", {"path": "note.txt"}, ctx)
        second = await execute("read_file", {"path": "note.txt"}, ctx)
        edited = await execute("edit_file", {"path": "note.txt", "old": "alpha", "new": "beta"}, ctx)
        third = await execute("read_file", {"path": "note.txt"}, ctx)
        return first.text, second.text, edited.ok, third.text

    first, second, edited, third = asyncio.run(scenario())
    assert "alpha" in first
    assert second == FILE_UNCHANGED
    assert edited
    assert "beta" in third
    assert third != FILE_UNCHANGED


def test_compact_restores_recent_files_missing_from_the_tail(tmp_path):
    from app.agent.edits import path_key, remember_read
    from app.agent.loop import restored_file_note

    kept = tmp_path / "kept.py"
    fresh = tmp_path / "fresh.py"
    kept.write_text("print('kept')\n", encoding="utf-8")
    fresh.write_text("print('fresh')\n", encoding="utf-8")
    remember_read(kept, kept.read_text(encoding="utf-8"), False, reusable=True, offset=1, limit=2000)
    remember_read(fresh, fresh.read_text(encoding="utf-8"), False, reusable=True, offset=1, limit=2000)
    note = restored_file_note([path_key(kept)], occupied=0)
    assert "fresh.py" in note
    assert "print('fresh')" in note
    assert "print('kept')" not in note


def test_edits_after_compact_refresh_the_summary(db, tmp_path, monkeypatch):
    import asyncio

    from app.agent import loop as loop_mod
    from app.agent.loop import _message_size, build_model_messages, iter_events

    async def fake_collect(provider, model, prompt, tools=None, max_tokens=128):
        return "# 当前状态\n_斜体说明_\n已经写入"

    monkeypatch.setattr("app.agent.providers.collect_text", fake_collect)

    async def scenario():
        conversation = Conversation(
            title="压缩后改文件",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(tmp_path),
            summary="尚未修改任何代码",
        )
        db.add(conversation)
        db.commit()
        bulky = "x" * 60_000
        db.add(Message(conversation_id=conversation.id, role="user", content=bulky, status="done"))
        db.add(Message(conversation_id=conversation.id, role="assistant", content="已读", status="done"))
        db.add(Message(conversation_id=conversation.id, role="user", content="继续", status="done"))
        db.add(Message(conversation_id=conversation.id, role="assistant", content="好", status="done"))
        db.commit()
        before = _message_size(build_model_messages(db, conversation, tmp_path))
        loop_mod.COMPACT_CHARS = before - len(bulky) + 20_000
        hub.override = ScriptedProvider(
            [
                {"content": "尚未修改"},
                {"tool_calls": [{"name": "write_file", "arguments": {"path": "note.txt", "content": "hello-ledger\n"}}]},
                {"content": "完成"},
                {"content": "已经写入 note.txt"},
            ]
        )
        async for _event in iter_events(db, conversation, "请写入 note.txt", []):
            pass
        db.refresh(conversation)
        return conversation.summary, (tmp_path / "note.txt").read_text(encoding="utf-8")

    previous = loop_mod.COMPACT_CHARS
    try:
        summary, body = asyncio.run(scenario())
    finally:
        loop_mod.COMPACT_CHARS = previous
    assert body == "hello-ledger\n"
    assert summary == "已经写入 note.txt"
