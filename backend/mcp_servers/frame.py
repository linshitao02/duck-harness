"""给本机 MCP 服务用的 Content-Length 帧。"""

import json
import sys


def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        decoded = line.decode("utf-8", errors="replace")
        if ":" in decoded:
            key, value = decoded.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length") or "0")
    body = sys.stdin.buffer.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def serve(instructions: str, tools: list[dict], call) -> None:
    while True:
        message = read_message()
        if message is None:
            return
        if "id" not in message:
            continue
        method = message.get("method")
        message_id = message["id"]
        if method == "initialize":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "duck-harness", "version": "1"},
                        "instructions": instructions,
                    },
                }
            )
            continue
        if method == "tools/list":
            write_message({"jsonrpc": "2.0", "id": message_id, "result": {"tools": tools}})
            continue
        if method == "tools/call":
            params = message.get("params") or {}
            name = str(params.get("name") or "")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            try:
                text = call(name, arguments)
                result = {"content": [{"type": "text", "text": text}]}
            except Exception as exc:
                result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            write_message({"jsonrpc": "2.0", "id": message_id, "result": result})
            continue
        write_message(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32601, "message": "unknown method"},
            }
        )
