"""流式工具、缓存重试、终端、ripgrep、LSP、远程 MCP、子代理邮箱。"""

import asyncio
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from app.agent.context import workspace_brief
from app.agent.executor import StreamingToolExecutor
from app.agent.loop import run_loop, send_mail
from app.agent.mcp_bridge import Snapshot
from app.agent.providers import OpenAICompatProvider, ScriptedProvider
from app.agent.tools import Tool, ToolContext, ToolResult, execute, schemas
from app.agent import mcp_bridge, mcp_client


def test_streaming_executor_overlaps_safe_tools():
    order = []

    async def runner(item):
        order.append(("start", item["id"]))
        await asyncio.sleep(0.15)
        order.append(("end", item["id"]))
        return ToolResult(True, item["id"])

    async def scenario():
        executor = StreamingToolExecutor(ToolContext(None, Path("."), None, "auto", None, None, "p", "m"), runner=runner)
        await executor.add({"id": "a", "name": "read_file", "arguments": {}})
        await executor.add({"id": "b", "name": "grep", "arguments": {}})
        results = await executor.finish()
        return [item.text for item in results]

    assert asyncio.run(scenario()) == ["a", "b"]
    assert order.index(("start", "b")) < order.index(("end", "a"))


def test_streaming_executor_serializes_mutations():
    order = []

    async def runner(item):
        order.append(("start", item["id"]))
        await asyncio.sleep(0.05)
        order.append(("end", item["id"]))
        return ToolResult(True, item["name"])

    async def scenario():
        executor = StreamingToolExecutor(ToolContext(None, Path("."), None, "auto", None, None, "p", "m"), runner=runner)
        await executor.add({"id": "a", "name": "edit_file", "arguments": {}})
        await executor.add({"id": "b", "name": "bash", "arguments": {}})
        return await executor.finish()

    asyncio.run(scenario())
    assert order == [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")]


def test_readonly_subagents_overlap_and_general_waits():
    from app.agent.executor import is_concurrency_safe

    assert is_concurrency_safe("spawn_agent", {"agent": "Explore"})
    assert is_concurrency_safe("spawn_agent", {"subagent_type": "plan"})
    assert not is_concurrency_safe("spawn_agent", {"agent": "general"})
    assert not is_concurrency_safe("spawn_agent", {"resume": "abc"})
    order = []

    async def runner(item):
        order.append(("start", item["id"]))
        await asyncio.sleep(0.05)
        order.append(("end", item["id"]))
        return ToolResult(True, item["id"])

    async def scenario():
        executor = StreamingToolExecutor(ToolContext(None, Path("."), None, "auto", None, None, "p", "m"), runner=runner)
        await executor.add({"id": "e1", "name": "spawn_agent", "arguments": {"agent": "explore", "task": "看结构"}})
        await executor.add({"id": "p1", "name": "spawn_agent", "arguments": {"agent": "plan", "task": "写方案"}})
        await executor.add({"id": "g1", "name": "spawn_agent", "arguments": {"agent": "general", "task": "改代码"}})
        await executor.add({"id": "e2", "name": "spawn_agent", "arguments": {"agent": "explore", "task": "再看一眼"}})
        return await executor.finish()

    asyncio.run(scenario())
    assert order.index(("start", "p1")) < order.index(("end", "e1"))
    assert order.index(("end", "e1")) < order.index(("start", "g1"))
    assert order.index(("end", "p1")) < order.index(("start", "g1"))
    assert order.index(("end", "g1")) < order.index(("start", "e2"))


def test_concurrent_tools_stop_at_ten():
    from app.agent.executor import MAX_TOOL_USE_CONCURRENCY

    started = []
    release = asyncio.Event()

    async def runner(item):
        started.append(item["id"])
        await release.wait()
        return ToolResult(True, item["id"])

    async def scenario():
        executor = StreamingToolExecutor(ToolContext(None, Path("."), None, "auto", None, None, "p", "m"), runner=runner)
        for index in range(MAX_TOOL_USE_CONCURRENCY):
            await executor.add({"id": str(index), "name": "read_file", "arguments": {}})
        for _ in range(50):
            if len(started) >= MAX_TOOL_USE_CONCURRENCY:
                break
            await asyncio.sleep(0.01)
        extra = asyncio.create_task(executor.add({"id": "extra", "name": "grep", "arguments": {}}))
        await asyncio.sleep(0.05)
        blocked = len(started) == MAX_TOOL_USE_CONCURRENCY and "extra" not in started
        release.set()
        await extra
        await executor.finish()
        return blocked

    assert asyncio.run(scenario()) is True


def test_provider_retries_429_then_reads_cache_usage():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="busy")
        body = (
            'data: {"choices":[{"index":0,"delta":{"content":"好"}}]}\n\n'
            'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":8,"completion_tokens":1,'
            '"prompt_cache_hit_tokens":5,"prompt_cache_miss_tokens":3}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    provider = OpenAICompatProvider(
        "http://model.test/v1",
        "test-key",
        "deepseek",
        transport=httpx.MockTransport(handler),
    )

    async def collect():
        kinds = []
        async for event in provider.stream([{"role": "user", "content": "hi"}], None, "deepseek-chat"):
            kinds.append(event)
        return kinds

    started = time.monotonic()
    events = asyncio.run(collect())
    assert calls["n"] == 2
    assert time.monotonic() - started >= 0.4
    assert "".join(event.text for event in events if event.kind == "text") == "好"
    usage = next(event for event in events if event.kind == "usage")
    assert usage.cache_hit_tokens == 5
    assert usage.cache_miss_tokens == 3
    assert "cache_control" not in json.dumps([{"role": "user", "content": "hi"}])


def test_provider_yields_earlier_tool_before_stream_ends():
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"a","function":{"name":"read_file","arguments":"{}"}}]}}]}\n\n'
            'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"id":"b","function":{"name":"grep","arguments":"{}"}}]}}]}\n\n'
            'data: {"choices":[{"finish_reason":"tool_calls"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    provider = OpenAICompatProvider("http://model.test/v1", "test-key", "deepseek", transport=httpx.MockTransport(handler))

    async def collect():
        return [event async for event in provider.stream([{"role": "user", "content": "hi"}], [], "deepseek-chat")]

    events = asyncio.run(collect())
    names = [event.tool_name for event in events if event.kind == "tool_call"]
    assert names == ["read_file", "grep"]


def test_shell_keeps_directory_and_uses_server_python(ctx):
    nested = ctx.workspace / "nested"
    nested.mkdir()
    (ctx.workspace / "note.txt").write_text("from-disk\n", encoding="utf-8")
    quoted = str(nested).replace("'", "''")

    async def scenario():
        marked = await execute(
            "bash",
            {
                "command": f"$env:HARNESS_MARK='kept-value'; Set-Location -LiteralPath '{quoted}'; Write-Output $env:HARNESS_MARK",
                "timeout": 20,
            },
            ctx,
        )
        followed = await execute(
            "bash",
            {"command": 'Write-Output "MARK=[$env:HARNESS_MARK]"; (Get-Location).Path', "timeout": 20},
            ctx,
        )
        mixed = await execute(
            "bash",
            {
                "command": "Set-Location -LiteralPath '"
                + str(ctx.workspace).replace("'", "''")
                + "'; python -c \"print('from-python')\"; Get-ChildItem note.txt | Select-Object Name,Length; Get-Content -Raw -Encoding utf8 note.txt",
                "timeout": 30,
            },
            ctx,
        )
        alive = await execute("bash", {"command": "Write-Output still-alive", "timeout": 20}, ctx)
        which = await execute(
            "bash",
            {"command": 'python -c "import sys; print(sys.executable)"', "timeout": 20},
            ctx,
        )
        failed = await execute(
            "bash",
            {"command": 'python -c "import sys; raise SystemExit(3)"', "timeout": 20},
            ctx,
        )
        return marked, followed, mixed, alive, which, failed

    marked, followed, mixed, alive, which, failed = asyncio.run(scenario())
    assert marked.ok and "kept-value" in marked.text
    assert followed.ok and "kept-value" not in followed.text
    assert str(nested).lower() in followed.text.lower()
    assert mixed.ok and "from-python" in mixed.text and "note.txt" in mixed.text and "from-disk" in mixed.text
    assert alive.ok and "still-alive" in alive.text
    assert which.ok and str(Path(sys.executable).resolve()).lower() in which.text.lower()
    assert not failed.ok and "退出码 3" in failed.text


def test_grep_still_finds_file_name(ctx):
    async def scenario():
        (ctx.workspace / "keep.py").write_text("print('hello-marker')\n", encoding="utf-8")
        return await execute("grep", {"pattern": "hello-marker"}, ctx)

    found = asyncio.run(scenario())
    assert found.ok and "keep.py" in found.text


def test_session_memory_stays_in_one_conversation(tmp_path):
    from app.agent.memory import rebuild_notes, session_memory_path

    notes = rebuild_notes("# 当前状态\n正在记住这件对齐的事", "")
    path = session_memory_path(tmp_path, 7)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(notes, encoding="utf-8")
    assert "记住这件对齐的事" in path.read_text(encoding="utf-8")
    assert not session_memory_path(tmp_path, 8).exists()
    assert "session_memory" not in workspace_brief(tmp_path)["instructions"]


def test_subagent_depth_and_mailbox(ctx):
    async def scenario():
        blocked = await execute("spawn_agent", {"task": "改代码", "agent": "general"}, _child(ctx, 1))
        deeper = await execute("spawn_agent", {"task": "只看", "agent": "explore"}, _child(ctx, 2))
        from app.agent.providers import hub

        hub.override = ScriptedProvider([{"content": "只读结论"}])
        try:
            allowed = await execute("spawn_agent", {"task": "只看", "agent": "explore"}, _child(ctx, 1))
        finally:
            hub.override = None
        send_mail("box-1", "邮箱里的话")
        ctx.agent_run_id = "box-1"
        provider = ScriptedProvider([{"content": "收到"}])
        text = await run_loop([{"role": "user", "content": "开始"}], provider, ctx, max_steps=1)
        return blocked, deeper, allowed, text, provider

    blocked, deeper, allowed, text, provider = asyncio.run(scenario())
    assert not blocked.ok
    assert not deeper.ok
    assert allowed.ok and "只读结论" in allowed.text
    assert text == "收到"
    sent = json.dumps(provider.calls[0]["messages"], ensure_ascii=False)
    assert "邮箱里的话" in sent


def _child(ctx, depth: int) -> ToolContext:
    return ToolContext(
        db=None,
        workspace=ctx.workspace,
        conversation_id=None,
        permission_mode="auto",
        broker=None,
        emit=None,
        provider_name="deepseek",
        model="deepseek-chat",
        depth=depth,
        hub=ctx.hub,
        origin_workspace=ctx.workspace,
    )


def test_tool_search_replaces_a_large_mcp_list():
    tools = [
        Tool(f"mcp__demo__tool_{index}", f"做第 {index} 件演示", {"type": "object", "properties": {}}, None, True)
        for index in range(17)
    ]
    mcp_bridge._snapshot = Snapshot(
        at=1,
        key="demo",
        tools=tools,
        by_name={tool.name: tool for tool in tools},
        prompt="",
    )
    names = [item["function"]["name"] for item in schemas()]
    assert "tool_search" in names
    assert "call_mcp_tool" in names
    assert "mcp__demo__tool_0" not in names
    mcp_bridge.clear_snapshot()


def test_http_mcp_oauth_and_tool_list():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length)
            if self.path == "/token":
                body = b'{"access_token":"tok-1","token_type":"bearer"}'
                self._send(200, body)
                return
            if self.headers.get("Authorization") != "Bearer tok-1":
                self._send(401, b"unauthorized")
                return
            message = json.loads(raw.decode("utf-8"))
            if message.get("method") == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {}}
            elif message.get("method") == "tools/list":
                result = {"tools": [{"name": "ping", "description": "pong", "inputSchema": {"type": "object"}}]}
            else:
                result = {}
            payload = json.dumps({"jsonrpc": "2.0", "id": message.get("id"), "result": result}).encode("utf-8")
            self._send(200, payload, {"Mcp-Session-Id": "sess-1"})

        def _send(self, status, body, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = __import__("threading").Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        session = await mcp_client.connect(
            f"http://127.0.0.1:{port}/mcp",
            [],
            {"token_url": f"http://127.0.0.1:{port}/token", "client_id": "id", "client_secret": "secret"},
        )
        listed = await session.client.list_tools()
        return listed, session.client.token

    try:
        listed, token = asyncio.run(scenario())
        assert listed[0]["name"] == "ping"
        assert token == "tok-1"
    finally:
        mcp_client.discard_sessions()
        server.shutdown()


def test_lsp_fake_definition(ctx, monkeypatch):
    from app.agent import lsp_client

    fake = Path(__file__).with_name("lsp_fake.py")
    monkeypatch.setitem(lsp_client._SERVERS, ".py", (sys.executable, [str(fake)]))
    target = ctx.workspace / "sample.py"
    target.write_text("value = 1\n", encoding="utf-8")

    async def scenario():
        result = await execute("lsp", {"path": "sample.py", "action": "definition", "line": 1, "character": 1}, ctx)
        missing = await execute("lsp", {"path": "sample.md", "action": "definition"}, ctx)
        for session in list(lsp_client._clients.values()):
            await session.close()
        lsp_client._clients.clear()
        return result, missing

    result, missing = asyncio.run(scenario())
    assert result.ok and "definition.py" in result.text
    assert not missing.ok


def test_worktree_roundtrip_when_git_exists(ctx):
    import shutil

    if shutil.which("git") is None:
        pytest.skip("没有 git")

    async def git(*args):
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=str(ctx.workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return proc.returncode, (out or b"").decode(), (err or b"").decode()

    async def scenario():
        await git("init")
        (ctx.workspace / "README.md").write_text("base\n", encoding="utf-8")
        await git("-c", "user.email=test@example.com", "-c", "user.name=test", "add", "README.md")
        code, _out, err = await git(
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=test",
            "commit",
            "-m",
            "base",
        )
        if code != 0:
            return "skip", err
        entered = await execute("enter_worktree", {"name": "align"}, ctx)
        if not entered.ok:
            return "skip", entered.text
        left = await execute("exit_worktree", {}, ctx)
        return "ok", (ctx.workspace, ctx.origin_workspace, left)

    kind, payload = asyncio.run(scenario())
    if kind == "skip":
        pytest.skip(str(payload))
    workspace, origin, left = payload
    assert workspace == origin
    assert left.ok


def test_prompts_follow_clawd_sections():
    from app.agent.agents import agent_system_prompt, instructions
    from app.agent.prompt import system_prompt
    from app.agent.tools import schemas

    text = system_prompt(
        {
            "workspace": r"C:\work",
            "platform": "Windows",
            "permission_mode": "auto",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "git": "abc",
            "agents": "- explore：搜代码",
            "instructions": "项目约定",
            "memories": "无",
            "skills": "无",
            "mcp": "（没有接入 MCP 服务）",
            "tasks": "无",
            "summary": "无",
        }
    )
    for heading in ("# 系统", "# 环境", "# 做任务", "# 谨慎执行", "# 使用工具", "# 语气", "# 输出效率"):
        assert heading in text
    assert "不要原样再调一次" in text
    assert "read_file" in text and "不要用 bash" in text
    assert "文件路径:行号" in text
    assert "不要估计要花多久" in text
    assert "C:\\work" in text
    assert "别的会话互不影响" in text and "环境变量不跨命令保留" in text
    assert "只在用户明确说工作树或 worktree 时使用" in text
    assert "对得上某个子代理的说明" in text
    assert "超过 3 次查询" in text
    assert "用前台" in text and "run_in_background" in text
    assert "explore 和 plan 会一起执行" in text

    explore = instructions("explore")
    plan = instructions("plan")
    general = instructions("general")
    assert "禁止" in explore and "glob_files" in explore
    assert ".harness/plans/" in plan and "3 到 5" in plan
    assert ".harness/plans/" in text
    assert "不要镀金" in general
    wrapped = agent_system_prompt(type("S", (), {"prompt": explore})(), None)
    assert "绝对路径" in wrapped
    assert "PowerShell" in wrapped

    spawn = next(item for item in schemas() if item["function"]["name"] == "spawn_agent")
    description = spawn["function"]["description"]
    assert "不要派子代理" in description
    assert "用户看不到" in description
    assert "超过 3 次查询" in description
    assert "用前台" in description
    assert "一起执行" in description


def test_large_output_is_saved_and_skills_take_arguments(ctx):
    from app.agent.state import JOBS, tag_job, take_job_notices
    from app.agent.tools import _command_timeout, bound_output, substitute_arguments

    saved = bound_output("字" * 50_001, ctx.workspace, "bash")
    assert "完整内容已保存到" in saved
    assert "预览" in saved
    path = saved.split("完整内容已保存到 ", 1)[1].splitlines()[0].strip()
    assert Path(path).is_file()
    assert len(Path(path).read_text(encoding="utf-8")) == 50_001
    assert _command_timeout(None) == 120
    assert _command_timeout(9999) == 600
    assert substitute_arguments("处理 $ARGUMENTS 的第 $0 项", "新闻 摘要") == "处理 新闻 摘要 的第 新闻 项"
    assert substitute_arguments("没有占位符", "补充") == "没有占位符\n\nARGUMENTS: 补充"

    class Dummy:
        conversation_id = 7
        turn_token = "turn"

    JOBS["job-test"] = {"kind": "command", "done": True, "text": "完成", "code": 0, "notified": False}
    tag_job(JOBS["job-test"], Dummy())
    notice = take_job_notices(Dummy())
    assert "job-test" in notice and "完成" in notice
    assert take_job_notices(Dummy()) == ""
    JOBS.pop("job-test", None)


def test_image_read_returns_pixels(ctx):
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x04\x00\x09\xfb\x03\xfd\xe4\x90\x9b\x1a\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    target = ctx.workspace / "dot.png"
    target.write_bytes(png)

    async def scenario():
        return await execute("read_file", {"path": "dot.png"}, ctx)

    result = asyncio.run(scenario())
    assert result.ok
    assert result.image and result.image["media_type"] == "image/png"
    assert result.image["data"]


class _ContinueProvider:
    """父回合按脚本回答。子代理的系统提示单独延迟，用来卡住后台任务。"""

    def __init__(self, first_call: dict, child_text: str, marker: str):
        self.first_call = first_call
        self.child_text = child_text
        self.marker = marker
        self.calls: list[list] = []
        self.name = "scripted"

    async def stream(self, messages, tools, model, should_stop=None, max_tokens=None, query_source="foreground", fallback_model=""):
        from app.agent.providers import StreamEvent

        system = ""
        if messages and messages[0].get("role") == "system":
            system = str(messages[0].get("content") or "")
        if system.startswith("你是通用子代理"):
            await asyncio.sleep(0.8)
            yield StreamEvent(kind="text", text=self.child_text)
            yield StreamEvent(kind="usage", prompt_tokens=1, completion_tokens=1)
            return
        self.calls.append(messages)
        blob = json.dumps(messages, ensure_ascii=False)
        if len(self.calls) == 1:
            raw = json.dumps(self.first_call["arguments"], ensure_ascii=False)
            yield StreamEvent(
                kind="tool_call",
                index=0,
                tool_id="c1",
                tool_name=self.first_call["name"],
                arguments_delta=raw,
            )
        elif self.marker in blob:
            yield StreamEvent(kind="text", text=f"最终 {self.marker}")
        else:
            yield StreamEvent(kind="text", text="先停一下")
        yield StreamEvent(kind="usage", prompt_tokens=1, completion_tokens=1)


def test_background_command_notifies_and_continues(ctx):
    provider = _ContinueProvider(
        {"name": "bash", "arguments": {"command": "Start-Sleep -Seconds 1; Write-Output late-ok", "run_in_background": True}},
        "",
        "late-ok",
    )
    ctx.hub.override = provider
    ctx.conversation_id = 42
    ctx.turn_token = "turn-bg"

    async def scenario():
        return await run_loop([{"role": "user", "content": "后台跑一下"}], provider, ctx, max_steps=4)

    final = asyncio.run(scenario())
    assert final == "最终 late-ok"
    assert any("后台任务已经结束" in json.dumps(call, ensure_ascii=False) for call in provider.calls)


def test_background_subagent_notifies_and_continues(ctx):
    provider = _ContinueProvider(
        {
            "name": "spawn_agent",
            "arguments": {"task": "给出结论", "agent": "general", "run_in_background": True, "max_steps": 1},
        },
        "子代理结论",
        "子代理结论",
    )
    ctx.hub.override = provider
    ctx.conversation_id = 43
    ctx.turn_token = "turn-agent"

    async def scenario():
        return await run_loop([{"role": "user", "content": "派一个后台子代理"}], provider, ctx, max_steps=4)

    final = asyncio.run(scenario())
    assert final == "最终 子代理结论"
    assert any("后台任务已经结束" in json.dumps(call, ensure_ascii=False) for call in provider.calls)


def test_web_fetch_prompt_extracts_with_current_model(ctx, monkeypatch):
    async def fake_fetch(url, limit=1_000_000):
        return "页面标题是鸭架。后面还有一大段无关正文。"

    seen = {}

    async def fake_collect(provider, model, prompt, tools=None, max_tokens=128):
        seen["model"] = model
        seen["prompt"] = prompt
        return "鸭架"

    monkeypatch.setattr("app.agent.tools.fetch_url", fake_fetch)
    monkeypatch.setattr("app.agent.providers.collect_text", fake_collect)

    async def scenario():
        extracted = await execute(
            "web_fetch",
            {"url": "https://example.com/news", "prompt": "只要标题"},
            ctx,
        )
        plain = await execute("web_fetch", {"url": "https://example.com/news"}, ctx)
        return extracted, plain

    extracted, plain = asyncio.run(scenario())
    assert extracted.ok and extracted.text == "鸭架"
    assert seen["model"] == "deepseek-chat"
    assert "只要标题" in seen["prompt"] and "鸭架" in seen["prompt"]
    assert "无关正文" in plain.text


def test_lsp_hover_symbols_and_calls(ctx, monkeypatch):
    from app.agent import lsp_client

    fake = Path(__file__).with_name("lsp_fake.py")
    monkeypatch.setitem(lsp_client._SERVERS, ".py", (sys.executable, [str(fake)]))
    (ctx.workspace / "sample.py").write_text("value = 1\n", encoding="utf-8")

    async def scenario():
        hover = await execute("lsp", {"path": "sample.py", "action": "hover", "line": 1, "character": 1}, ctx)
        symbols = await execute("lsp", {"path": "sample.py", "action": "document_symbol"}, ctx)
        workspace = await execute("lsp", {"path": "sample.py", "action": "workspace_symbol", "query": "value"}, ctx)
        implementation = await execute("lsp", {"path": "sample.py", "action": "implementation"}, ctx)
        incoming = await execute("lsp", {"path": "sample.py", "action": "incoming_calls"}, ctx)
        outgoing = await execute("lsp", {"path": "sample.py", "action": "outgoing_calls"}, ctx)
        for session in list(lsp_client._clients.values()):
            await session.close()
        lsp_client._clients.clear()
        return hover, symbols, workspace, implementation, incoming, outgoing

    hover, symbols, workspace, implementation, incoming, outgoing = asyncio.run(scenario())
    assert hover.ok and "fake-hover" in hover.text
    assert symbols.ok and "fake-symbol" in symbols.text
    assert workspace.ok and "fake-workspace" in workspace.text
    assert implementation.ok and "implementation.py" in implementation.text
    assert incoming.ok and "fake-incoming" in incoming.text
    assert outgoing.ok and "fake-outgoing" in outgoing.text


def test_skill_runs_embedded_commands(ctx):
    skill = ctx.workspace / ".harness" / "skills" / "runner"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: runner\ndescription: 带入口\n---\n"
        "目录 ${CLAUDE_SKILL_DIR}\n"
        "行内 !`Write-Output skill-ran`\n"
        "```!\nWrite-Output block-ran\n```\n",
        encoding="utf-8",
    )
    danger = ctx.workspace / ".harness" / "skills" / "danger"
    danger.mkdir(parents=True)
    (danger / "SKILL.md").write_text(
        "---\nname: danger\ndescription: 危险\n---\n!`format C:`\n",
        encoding="utf-8",
    )
    writer = ctx.workspace / ".harness" / "skills" / "writer"
    writer.mkdir(parents=True)
    (writer / "SKILL.md").write_text(
        "---\nname: writer\ndescription: 会改文件\n---\n!`Set-Content -Path x.txt -Value hi`\n",
        encoding="utf-8",
    )

    async def scenario():
        loaded = await execute("use_skill", {"name": "runner"}, ctx)
        blocked = await execute("use_skill", {"name": "danger"}, ctx)
        ctx.permission_mode = "plan"
        refused = await execute("use_skill", {"name": "writer"}, ctx)
        return loaded, blocked, refused

    loaded, blocked, refused = asyncio.run(scenario())
    assert loaded.ok
    assert "skill-ran" in loaded.text and "block-ran" in loaded.text
    assert "!`" not in loaded.text and "```!" not in loaded.text
    assert str(skill).replace("\\", "/") in loaded.text
    assert not blocked.ok and "拒绝" in blocked.text
    assert not refused.ok
    assert not (ctx.workspace / "x.txt").exists()


def test_image_pixels_reach_only_vision_models(ctx):
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x04\x00\x09\xfb\x03\xfd\xe4\x90\x9b\x1a\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    (ctx.workspace / "dot.png").write_bytes(png)

    async def once(model: str):
        provider = ScriptedProvider(
            [
                {"tool_calls": [{"id": "r1", "name": "read_file", "arguments": {"path": "dot.png"}}]},
                {"content": "看过了"},
            ]
        )
        ctx.model = model
        ctx.hub.override = provider
        text = await run_loop([{"role": "user", "content": "看这张图"}], provider, ctx, max_steps=3)
        tool_messages = [
            message
            for call in provider.calls
            for message in call["messages"]
            if message.get("role") == "tool"
        ]
        return text, tool_messages[-1]["content"]

    async def scenario():
        plain = await once("deepseek-chat")
        vision = await once("deepseek-vl")
        return plain, vision

    plain, vision = asyncio.run(scenario())
    assert plain[0] == "看过了" and isinstance(plain[1], str) and "dot.png" in plain[1]
    assert vision[0] == "看过了"
    assert isinstance(vision[1], list)
    assert vision[1][1]["type"] == "image_url"
    assert vision[1][1]["image_url"]["url"].startswith("data:image/png;base64,")
