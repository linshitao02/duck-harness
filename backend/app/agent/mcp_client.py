import json
import os

import asyncio

import httpx


class McpClient:
    """最小的 MCP stdio 客户端：Content-Length 帧，initialize / tools/list / tools/call。"""

    def __init__(self, proc: asyncio.subprocess.Process):
        self.proc = proc
        self._next_id = 1

    async def initialize(self) -> dict:
        result = await self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "duck-harness", "version": "0.1.0"},
            },
        )
        await self.notify("notifications/initialized", {})
        return result

    async def list_tools(self) -> list[dict]:
        tools = []
        cursor = None
        for _ in range(20):
            params = {} if not cursor else {"cursor": cursor}
            result = await self.request("tools/list", params)
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor") or result.get("next_cursor")
            if not cursor:
                break
        return tools

    async def list_resources(self) -> list[dict]:
        result = await self.request("resources/list", {})
        return result.get("resources") or []

    async def read_resource(self, uri: str) -> str:
        result = await self.request("resources/read", {"uri": uri})
        chunks = []
        for block in result.get("contents") or []:
            if isinstance(block, dict) and block.get("text"):
                chunks.append(block["text"])
            elif isinstance(block, str):
                chunks.append(block)
        if chunks:
            return "\n".join(chunks)
        return json.dumps(result, ensure_ascii=False)

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self.request("tools/call", {"name": name, "arguments": arguments or {}})
        chunks = []
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("text"):
                chunks.append(block["text"])
            elif isinstance(block, str):
                chunks.append(block)
        if chunks:
            return "\n".join(chunks)
        return json.dumps(result, ensure_ascii=False)

    async def request(self, method: str, params: dict) -> dict:
        message_id = self._next_id
        self._next_id += 1
        await self._write({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
        while True:
            incoming = await self._read()
            if incoming.get("id") != message_id:
                continue
            if "error" in incoming:
                raise RuntimeError(str(incoming["error"]))
            return incoming.get("result") or {}

    async def notify(self, method: str, params: dict) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _write(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        assert self.proc.stdin is not None
        self.proc.stdin.write(header + body)
        await self.proc.stdin.drain()

    async def _read(self) -> dict:
        assert self.proc.stdout is not None
        headers: dict[str, str] = {}
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                raise RuntimeError("MCP 进程已结束")
            if line in (b"\r\n", b"\n"):
                break
            decoded = line.decode("utf-8", errors="replace")
            if ":" in decoded:
                key, value = decoded.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        length = int(headers.get("content-length") or "0")
        body = await self.proc.stdout.readexactly(length)
        return json.loads(body.decode("utf-8"))


def session_key(command: str, args: list[str] | None, env: dict | None) -> str:
    clean = {str(key): str(value) for key, value in (env or {}).items()}
    payload = {"command": command, "args": [str(item) for item in (args or [])], "env": clean}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _string_env(env: dict | None) -> dict[str, str]:
    merged = os.environ.copy()
    for key, value in (env or {}).items():
        merged[str(key)] = str(value)
    return merged


async def open_server(command: str, args: list[str], env: dict | None):
    proc = await asyncio.create_subprocess_exec(
        command,
        *list(args or []),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=_string_env(env),
    )
    client = McpClient(proc)
    return proc, client


def _kill_proc(proc) -> None:
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        return


def _detach_proc(proc) -> None:
    """事件循环已经关闭时，直接关掉管道，避免解释器回收时再报警。"""
    _kill_proc(proc)
    transport = getattr(proc, "_transport", None)
    if transport is None:
        return
    transport._closed = True
    pipes = getattr(transport, "_pipes", {}) or {}
    for proto in pipes.values():
        pipe = getattr(proto, "pipe", None)
        if pipe is None:
            continue
        sock = getattr(pipe, "_sock", None)
        pipe._sock = None
        pipe._closing = True
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


async def close_server(proc) -> None:
    if proc is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _detach_proc(proc)
        return
    if loop.is_closed():
        _detach_proc(proc)
        return
    if proc.stdin is not None and not proc.stdin.is_closing():
        try:
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError, OSError, RuntimeError):
            pass
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except (TimeoutError, ProcessLookupError, OSError):
            _kill_proc(proc)
    transport = getattr(proc, "_transport", None)
    if transport is not None and not getattr(transport, "_closed", False):
        try:
            transport.close()
            await asyncio.sleep(0)
        except RuntimeError:
            _detach_proc(proc)


class HttpMcpClient(McpClient):
    """Streamable HTTP：POST JSON-RPC，必要时用 client_credentials 换 token。"""

    def __init__(self, url: str, env: dict | None):
        self.proc = None
        self._next_id = 1
        self.url = url
        self.env = {str(key): str(value) for key, value in (env or {}).items()}
        self.session_id = ""
        self.token = self.env.get("MCP_TOKEN") or self.env.get("token") or ""

    async def _oauth(self, client: httpx.AsyncClient) -> None:
        token_url = self.env.get("token_url") or ""
        if not token_url:
            raise RuntimeError("MCP 需要登录，但没有配置 token_url")
        response = await client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self.env.get("client_id", ""),
                "client_secret": self.env.get("client_secret", ""),
            },
        )
        response.raise_for_status()
        self.token = str(response.json().get("access_token") or "")
        if not self.token:
            raise RuntimeError("MCP 登录没有返回 access_token")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _decode(self, response: httpx.Response) -> dict:
        if response.headers.get("mcp-session-id"):
            self.session_id = response.headers["mcp-session-id"]
        ctype = response.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            for line in response.text.splitlines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                obj = json.loads(data)
                if "result" in obj or "error" in obj:
                    return obj
            raise RuntimeError("MCP HTTP 没有返回结果")
        return response.json()

    async def request(self, method: str, params: dict) -> dict:
        message_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params}
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(self.url, headers=self._headers(), json=payload)
            if response.status_code == 401:
                await self._oauth(client)
                response = await client.post(self.url, headers=self._headers(), json=payload)
            if response.status_code >= 400:
                raise RuntimeError(f"MCP HTTP {response.status_code}: {response.text[:300]}")
            incoming = self._decode(response)
        if incoming.get("id") not in {None, message_id}:
            raise RuntimeError("MCP HTTP 响应序号不一致")
        if "error" in incoming:
            raise RuntimeError(str(incoming["error"]))
        return incoming.get("result") or {}

    async def notify(self, method: str, params: dict) -> None:
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(self.url, headers=self._headers(), json=payload)


class McpSession:
    def __init__(self, key: str, proc, client: McpClient, info: dict):
        self.key = key
        self.proc = proc
        self.client = client
        self.info = info
        self.lock = asyncio.Lock()
        self.loop = asyncio.get_running_loop()


_sessions: dict[str, McpSession] = {}
_create_locks: dict[str, asyncio.Lock] = {}
_registry_lock: asyncio.Lock | None = None


def discard_sessions() -> None:
    global _registry_lock
    for session in list(_sessions.values()):
        _detach_proc(session.proc)
    _sessions.clear()
    _create_locks.clear()
    _registry_lock = None


def _registry() -> asyncio.Lock:
    global _registry_lock
    if _registry_lock is None:
        _registry_lock = asyncio.Lock()
    return _registry_lock


def _alive(key: str) -> McpSession | None:
    session = _sessions.get(key)
    if session is None:
        return None
    loop = asyncio.get_running_loop()
    if session.loop is not loop:
        return None
    if session.proc is not None and session.proc.returncode is not None:
        return None
    return session


async def drop(key: str) -> None:
    async with _registry():
        session = _sessions.pop(key, None)
    if session is not None:
        await close_server(session.proc)


async def prune(keep: set[str]) -> None:
    async with _registry():
        stale = [key for key in _sessions if key not in keep]
        gone = [_sessions.pop(key) for key in stale]
    for session in gone:
        await close_server(session.proc)


async def connect(command: str, args: list[str] | None, env: dict | None) -> McpSession:
    key = session_key(command, args, env)
    async with _registry():
        current = _alive(key)
        if current is not None:
            return current
        stale = _sessions.pop(key, None)
        create_lock = _create_locks.setdefault(key, asyncio.Lock())
    if stale is not None:
        await close_server(stale.proc)
    async with create_lock:
        async with _registry():
            current = _alive(key)
            if current is not None:
                return current
        if str(command).startswith(("http://", "https://")):
            client = HttpMcpClient(command, env)
            proc = None
        else:
            proc, client = await open_server(command, list(args or []), env)
        try:
            info = await client.initialize()
        except Exception:
            await close_server(proc)
            raise
        session = McpSession(key, proc, client, info if isinstance(info, dict) else {})
        async with _registry():
            previous = _sessions.get(key)
            if previous is not None and previous is not session and previous.proc.returncode is None and previous.loop is session.loop:
                await close_server(proc)
                return previous
            _sessions[key] = session
        return session


async def operate(command: str, args: list[str] | None, env: dict | None, fn):
    last_error: Exception | None = None
    for attempt in range(2):
        session = await connect(command, args, env)
        try:
            async with session.lock:
                if session.proc is not None and session.proc.returncode is not None:
                    raise RuntimeError("MCP 进程已结束")
                return await fn(session.client)
        except RuntimeError as exc:
            last_error = exc
            dead = (session.proc is not None and session.proc.returncode is not None) or "进程已结束" in str(exc)
            if dead:
                await drop(session.key)
            if not dead or attempt == 1:
                raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("MCP 进程已结束")
