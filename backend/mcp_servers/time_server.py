"""本机时间 MCP。时区使用固定偏移，不考虑夏令时。"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frame import serve

OFFSETS = {
    "UTC": 0,
    "Asia/Shanghai": 8,
    "Asia/Tokyo": 9,
    "Asia/Singapore": 8,
    "Europe/London": 0,
    "Europe/Paris": 1,
    "America/New_York": -5,
    "America/Los_Angeles": -8,
}


def _zone(name: str):
    key = (name or "UTC").strip() or "UTC"
    if key not in OFFSETS:
        known = "、".join(OFFSETS)
        raise ValueError(f"不认识时区 {key}。可用：{known}")
    return key, timezone(timedelta(hours=OFFSETS[key]))


def call(name: str, arguments: dict) -> str:
    if name == "now":
        zone_name = str(arguments.get("timezone") or "").strip()
        if not zone_name:
            current = datetime.now().astimezone()
            return current.isoformat(timespec="seconds")
        key, zone = _zone(zone_name)
        current = datetime.now(zone)
        return f"{key} {current.isoformat(timespec='seconds')}"
    if name == "convert_time":
        raw = str(arguments.get("time") or "").strip()
        if not raw:
            raise ValueError("需要 time，格式如 2026-01-01T08:00:00")
        source_name, source = _zone(str(arguments.get("from_timezone") or "UTC"))
        target_name, target = _zone(str(arguments.get("to_timezone") or "UTC"))
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=source)
        else:
            parsed = parsed.astimezone(source)
        converted = parsed.astimezone(target)
        return f"{source_name} -> {target_name} {converted.isoformat(timespec='seconds')}"
    raise ValueError(f"没有这个工具: {name}")


if __name__ == "__main__":
    serve(
        "需要当前时间或把一个时间换到另一个时区时，调用 now 或 convert_time。时区是固定偏移，不考虑夏令时。",
        [
            {
                "name": "now",
                "description": "返回当前时间。timezone 留空则用本机时区。",
                "inputSchema": {
                    "type": "object",
                    "properties": {"timezone": {"type": "string", "description": "如 Asia/Shanghai 或 UTC"}},
                },
            },
            {
                "name": "convert_time",
                "description": "把一个本地时间从 from_timezone 换到 to_timezone。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "time": {"type": "string"},
                        "from_timezone": {"type": "string"},
                        "to_timezone": {"type": "string"},
                    },
                    "required": ["time", "from_timezone", "to_timezone"],
                },
            },
        ],
        call,
    )
