"""测试用的最小语言服务：回答定义、引用、诊断，以及悬停、符号和调用层次。"""

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
    data = json.dumps(payload).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)
    sys.stdout.buffer.flush()


def main() -> None:
    while True:
        message = read_message()
        if not message:
            return
        if "id" not in message:
            continue
        method = message.get("method")
        if method == "initialize":
            result = {"capabilities": {"diagnosticProvider": {"interFileDependencies": False}}}
        elif method == "textDocument/definition":
            result = [{"uri": "file:///definition.py", "range": {"start": {"line": 0, "character": 0}}}]
        elif method == "textDocument/references":
            result = [{"uri": "file:///references.py"}]
        elif method == "textDocument/diagnostic":
            result = {"items": [{"message": "fake-diagnostic"}]}
        elif method == "textDocument/hover":
            result = {"contents": "fake-hover"}
        elif method == "textDocument/documentSymbol":
            result = [{"name": "fake-symbol"}]
        elif method == "workspace/symbol":
            result = [{"name": "fake-workspace"}]
        elif method == "textDocument/implementation":
            result = [{"uri": "file:///implementation.py"}]
        elif method == "textDocument/prepareCallHierarchy":
            result = [{"name": "fake-item"}]
        elif method == "callHierarchy/incomingCalls":
            result = [{"from": {"name": "fake-incoming"}}]
        elif method == "callHierarchy/outgoingCalls":
            result = [{"to": {"name": "fake-outgoing"}}]
        else:
            result = {}
        write_message({"jsonrpc": "2.0", "id": message["id"], "result": result})


if __name__ == "__main__":
    main()
