"""每条命令新开一次 PowerShell，读到进程退出为止。

常驻会话会在 Invoke-Expression 之后立刻写结束标记，外部程序和 Select-Object
写在同一次执行里时，成功输出还没刷到管道上，标记已经先到了。改成一条命令一个进程，
输出随进程结束一起收齐。工作目录记在命令末尾，下一条命令接着用。环境变量不跨命令保留。
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_cwd: dict[str, Path] = {}
_locks: dict[str, asyncio.Lock] = {}
_live: dict[int, asyncio.subprocess.Process] = {}


def shell_key(workspace: Path, conversation_id: int | None, agent_run_id: str = "") -> str:
    """同一工作区里，每个会话记住自己的当前目录。子代理不带动主会话的目录。"""
    try:
        convo = str(int(conversation_id)) if conversation_id is not None else "0"
    except (TypeError, ValueError):
        convo = "0"
    agent = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (agent_run_id or "main"))[:40] or "main"
    return f"{workspace.resolve()}|{convo}|{agent}"


def current_cwd(workspace: Path, key: str) -> Path:
    root = workspace.resolve()
    cwd = _cwd.get(key, root)
    if not cwd.is_dir():
        return root
    return cwd


def child_env() -> dict[str, str]:
    """子进程的 python 使用正在跑服务的解释器，避免落到 Anaconda 主环境。"""
    env = os.environ.copy()
    bindir = str(Path(sys.executable).resolve().parent)
    path_key = next((key for key in env if key.upper() == "PATH"), "PATH")
    current = env.get(path_key, "")
    kept: list[str] = []
    for piece in current.split(os.pathsep):
        if not piece:
            continue
        try:
            same = Path(piece).resolve() == Path(bindir)
        except OSError:
            same = False
        if not same:
            kept.append(piece)
    env[path_key] = os.pathsep.join([bindir, *kept])
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _script(command: str, cwd_file: Path) -> str:
    escaped = str(cwd_file).replace("'", "''")
    # 本机程序写过错误流时 $? 会是假，即使退出码是 0。先看 $LASTEXITCODE。
    # 不用 exit：它会在格式化输出刷出来之前结束进程，python 后面的 Select-Object 就看不见了。
    return (
        "[Console]::InputEncoding = New-Object System.Text.UTF8Encoding $false\n"
        "[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false\n"
        "$OutputEncoding = [Console]::OutputEncoding\n"
        f"{command}\n"
        "$_ec = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } elseif ($?) { 0 } else { 1 }\n"
        f"[System.IO.File]::WriteAllText('{escaped}', (Get-Location).Path)\n"
        "$host.SetShouldExit($_ec)\n"
    )


def _lock(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def _temp(prefix: str, suffix: str) -> Path:
    handle, name = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(handle)
    return Path(name)


def _unlink(path: Path) -> None:
    for _ in range(8):
        try:
            path.unlink(missing_ok=True)
            return
        except OSError:
            time.sleep(0.05)


def _kill_pid(pid: int | None) -> None:
    if not pid:
        return
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
        check=False,
    )


def _remember_cwd(key: str, cwd_file: Path, fallback: Path) -> None:
    try:
        text = cwd_file.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return
    if not text:
        return
    found = Path(text)
    if found.is_dir():
        _cwd[key] = found
    else:
        _cwd[key] = fallback


async def _run_once(cwd: Path, command: str, timeout: int, cwd_file: Path) -> tuple[int, str]:
    script_path = _temp("duck-ps-", ".ps1")
    script_path.write_bytes(b"\xef\xbb\xbf" + _script(command, cwd_file).encode("utf-8"))
    proc = await asyncio.create_subprocess_exec(
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(script_path),
        cwd=str(cwd),
        env=child_env(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    _live[id(proc)] = proc
    try:
        try:
            stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            _kill_pid(proc.pid)
            try:
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
            raise
        text = (stdout or b"").decode("utf-8", errors="replace").strip()
        code = proc.returncode if proc.returncode is not None else 1
        return code, text
    finally:
        _live.pop(id(proc), None)
        _unlink(script_path)


async def run_shell(workspace: Path, command: str, timeout: int, session_key: str | None = None) -> tuple[int, str]:
    root = workspace.resolve()
    key = session_key or shell_key(workspace, None)
    cwd_file = _temp("duck-pwd-", ".txt")
    try:
        async with _lock(key):
            cwd = current_cwd(root, key)
            code, text = await _run_once(cwd, command, timeout, cwd_file)
            _remember_cwd(key, cwd_file, cwd)
            return code, text
    finally:
        _unlink(cwd_file)


def discard_shells() -> None:
    _cwd.clear()
    _locks.clear()
    procs = list(_live.values())
    _live.clear()
    for proc in procs:
        _kill_pid(proc.pid)
