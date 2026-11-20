"""模型还在输出时就开始跑已经解析完的工具。

只读工具，以及只读的 explore、plan 子代理，可以彼此并行，同时最多 10 个。
会改文件、跑命令，或 general 子代理，要等正在跑的工具结束，并且单独执行。
结果仍按模型给出的顺序交回。
"""

import asyncio

from .tools import ToolResult, execute

MAX_TOOL_USE_CONCURRENCY = 10
READONLY = {
    "read_file",
    "glob_files",
    "grep",
    "list_dir",
    "git_summary",
    "system_info",
    "web_search",
    "web_fetch",
    "list_memories",
    "list_tasks",
    "use_skill",
    "list_mcp_tools",
    "list_mcp_resources",
    "read_mcp_resource",
    "tool_search",
    "sleep",
    "lsp",
}


def is_concurrency_safe(name: str, arguments: dict | None = None) -> bool:
    if name in READONLY:
        return True
    if name != "spawn_agent":
        return False
    args = arguments or {}
    if str(args.get("resume") or "").strip() and not (args.get("agent") or args.get("subagent_type")):
        return False
    from .agents import normalize_kind

    kind = normalize_kind(str(args.get("agent") or args.get("subagent_type") or ""))
    return kind in {"explore", "plan"}


class StreamingToolExecutor:
    def __init__(self, ctx, on_start=None, runner=None):
        self.ctx = ctx
        self.on_start = on_start
        self.runner = runner or self._execute
        self.items: list[dict] = []
        self.results: dict[str, ToolResult] = {}
        self._inflight: list[asyncio.Task] = []
        self._exclusive = False

    def _stopped(self) -> bool:
        from .loop import _stopped

        key = getattr(self.ctx, "conversation_key", None)
        token = getattr(self.ctx, "turn_token", "")
        return bool(key) and _stopped(key, token)

    async def add(self, item: dict) -> None:
        self.items.append(item)
        if self._stopped():
            return
        if is_concurrency_safe(item["name"], item.get("arguments")) and not self._exclusive:
            await self._acquire_slot()
            self._inflight.append(asyncio.create_task(self._run(item)))
            return
        self._exclusive = True
        await self._wait()
        if not self._stopped():
            await self._run(item)
        self._exclusive = False

    async def finish(self) -> list[ToolResult]:
        await self._wait()
        ordered = []
        for item in self.items:
            result = self.results.get(item["id"])
            if result is None:
                result = ToolResult(False, "这次请求被中断了")
            ordered.append(result)
        return ordered

    async def _acquire_slot(self) -> None:
        while True:
            pending = [task for task in self._inflight if not task.done()]
            self._inflight = pending
            if len(pending) < MAX_TOOL_USE_CONCURRENCY:
                return
            await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)

    async def _wait(self) -> None:
        pending = [task for task in self._inflight if not task.done()]
        if pending:
            await asyncio.gather(*pending)
        self._inflight = []

    async def _execute(self, item: dict) -> ToolResult:
        return await execute(item["name"], item["arguments"], self.ctx)

    async def _run(self, item: dict) -> None:
        if self.on_start:
            try:
                await self.on_start(item)
            except Exception:
                pass
        try:
            result = await self.runner(item)
        except Exception as exc:
            result = ToolResult(False, str(exc))
        self.results[item["id"]] = result
