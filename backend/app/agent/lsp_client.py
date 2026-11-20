"""按文件类型拉起语言服务，提供定义、引用和诊断。没有安装对应服务时明确告诉模型。"""

import asyncio
import json
import shutil
from pathlib import Path

_SERVERS = {
    ".py": ("pyright-langserver", ["--stdio"]),
    ".pyi": ("pyright-langserver", ["--stdio"]),
    ".ts": ("typescript-language-server", ["--stdio"]),
    ".tsx": ("typescript-language-server", ["--stdio"]),
    ".js": ("typescript-language-server", ["--stdio"]),
    ".jsx": ("typescript-language-server", ["--stdio"]),
}

_clients: dict[tuple[str, str], "LspSession"] = {}


class LspSession:
    def __init__(self, root: Path, command: str, args: list[str]):
        self.root = root
        self.command = command
        self.args = args
        self.proc: asyncio.subprocess.Process | None = None
        self._next = 1
        self.lock = asyncio.Lock()
        self._opened: set[str] = set()

    async def start(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            return
        binary = shutil.which(self.command)
        if not binary:
            raise RuntimeError(f"没有安装语言服务 {self.command}")
        self.proc = await asyncio.create_subprocess_exec(
            binary,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await self.request(
            "initialize",
            {
                "processId": None,
                "rootUri": self.root.resolve().as_uri(),
                "capabilities": {"textDocument": {"diagnostic": {}}},
                "workspaceFolders": [{"uri": self.root.resolve().as_uri(), "name": self.root.name}],
            },
        )
        await self.notify("initialized", {})

    async def open_file(self, path: Path) -> None:
        key = str(path.resolve())
        if key in self._opened:
            return
        text = path.read_text(encoding="utf-8", errors="replace")
        await self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": path.resolve().as_uri(),
                    "languageId": path.suffix.lstrip(".") or "plaintext",
                    "version": 1,
                    "text": text,
                }
            },
        )
        self._opened.add(key)

    async def request(self, method: str, params: dict):
        await self.start()
        message_id = self._next
        self._next += 1
        await self._write({"jsonrpc": "2.0", "id": message_id, "method": method, "params": params})
        while True:
            incoming = await self._read()
            if incoming.get("id") != message_id:
                continue
            if "error" in incoming:
                raise RuntimeError(str(incoming["error"]))
            return incoming.get("result")

    async def notify(self, method: str, params: dict) -> None:
        await self.start()
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _write(self, payload: dict) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self.proc.stdin.write(header + body)
        await self.proc.stdin.drain()

    async def _read(self) -> dict:
        assert self.proc is not None and self.proc.stdout is not None
        headers: dict[str, str] = {}
        while True:
            line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=20)
            if not line:
                raise RuntimeError("语言服务已退出")
            if line in (b"\r\n", b"\n"):
                break
            decoded = line.decode("utf-8", errors="replace")
            if ":" in decoded:
                key, value = decoded.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        length = int(headers.get("content-length") or "0")
        body = await asyncio.wait_for(self.proc.stdout.readexactly(length), timeout=20)
        return json.loads(body.decode("utf-8"))

    async def close(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                return


def server_for(path: Path) -> tuple[str, list[str]] | None:
    found = _SERVERS.get(path.suffix.lower())
    return found


async def session_for(root: Path, path: Path) -> LspSession:
    spec = server_for(path)
    if spec is None:
        raise RuntimeError(f"没有为 {path.suffix or '该文件'} 配置语言服务")
    command, args = spec
    key = (str(root.resolve()), command)
    session = _clients.get(key)
    if session is None:
        session = LspSession(root, command, args)
        _clients[key] = session
    async with session.lock:
        await session.start()
        await session.open_file(path)
    return session


def discard_lsp() -> None:
    sessions = list(_clients.values())
    _clients.clear()
    for session in sessions:
        proc = session.proc
        session.proc = None
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                continue
