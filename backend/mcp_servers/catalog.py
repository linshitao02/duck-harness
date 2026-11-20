"""随项目提供的四个本机 MCP 服务。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def builtin_servers(python: str | None = None) -> list[dict]:
    executable = python or sys.executable
    specs = (
        ("time", "time_server.py"),
        ("calc", "calc_server.py"),
        ("text", "text_server.py"),
        ("units", "units_server.py"),
    )
    return [
        {"name": name, "command": executable, "args": [str(ROOT / filename)], "env": {}}
        for name, filename in specs
    ]
