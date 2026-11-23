from pathlib import Path

from ..config import ENV_FILE

SENSITIVE_DIR = Path(r"C:\Users\shita\Desktop\账号密码")


def _norm(path: Path) -> str:
    try:
        resolved = str(path.resolve()).lower()
    except OSError:
        resolved = str(path).lower()
    return resolved.replace("/", "\\")


def sensitive_reason(path: Path) -> str | None:
    target = _norm(path)
    blocks = [
        (_norm(SENSITIVE_DIR), "账号密码目录"),
        (_norm(ENV_FILE), "后端环境文件"),
    ]
    for prefix, label in blocks:
        if target == prefix or target.startswith(prefix + "\\"):
            return f"拒绝访问{label}"
    name = path.name.lower()
    if name in {".env", "deepseek-key.txt"}:
        return "拒绝访问密钥文件"
    return None


def blocked_command(command: str) -> str | None:
    from ..access import command_touches_foreign
    from .pwshsec import command_block_reason

    return command_block_reason(command) or command_touches_foreign(command)
