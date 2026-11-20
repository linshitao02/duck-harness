"""最小 MCP 服务：回显 text 参数。测试用，走 Content-Length 帧。"""

import json
import os
import sys

TOOL_NAME = os.environ.get("MCP_TOOL", "echo")
SERVER_LABEL = os.environ.get("MCP_LABEL", "")
COUNT_CALLS = bool(os.environ.get("MCP_COUNT"))
CALLS = 0


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


def write_message(payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def main():
    global CALLS
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
                        "serverInfo": {"name": "echo", "version": "0.1"},
                        "instructions": "这是回显服务。需要原样返回文本时使用 echo。",
                    },
                }
            )
        elif method == "tools/list":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {
                        "tools": [
                            {
                                "name": TOOL_NAME,
                                "description": "回显文本" if not SERVER_LABEL else f"回显文本，服务标记 {SERVER_LABEL}",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"text": {"type": "string"}},
                                    "required": ["text"],
                                },
                            }
                        ]
                    },
                }
            )
        elif method == "resources/list":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {"resources": [{"uri": "echo://note", "name": "note"}]},
                }
            )
        elif method == "resources/read":
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "result": {"contents": [{"uri": "echo://note", "text": "resource-ok"}]},
                }
            )
        elif method == "tools/call":
            params = message.get("params") or {}
            arguments = params.get("arguments") or {}
            called = str(params.get("name") or "")
            text = str(arguments.get("text", ""))
            if called != TOOL_NAME:
                result = {"content": [{"type": "text", "text": f"wrong-tool:{called}"}], "isError": True}
            elif text == "__error__":
                result = {"content": [{"type": "text", "text": "echo failed"}], "isError": True}
            else:
                CALLS += 1
                body = f"{SERVER_LABEL}/{called}:{text}" if SERVER_LABEL else text
                if COUNT_CALLS:
                    body = f"{body}#{CALLS}"
                result = {"content": [{"type": "text", "text": body}]}
            write_message({"jsonrpc": "2.0", "id": message_id, "result": result})
        else:
            write_message(
                {
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "error": {"code": -32601, "message": "unknown method"},
                }
            )


if __name__ == "__main__":
    main()
