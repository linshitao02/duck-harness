"""编码、摘要和标识 MCP。"""

import base64
import hashlib
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frame import serve


def call(name: str, arguments: dict) -> str:
    text = str(arguments.get("text") or "")
    if name == "base64_encode":
        return base64.b64encode(text.encode("utf-8")).decode("ascii")
    if name == "base64_decode":
        try:
            return base64.b64decode(text, validate=True).decode("utf-8")
        except Exception as exc:
            raise ValueError(f"无法解码: {exc}") from exc
    if name == "sha256":
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    if name == "new_uuid":
        return str(uuid.uuid4())
    raise ValueError(f"没有这个工具: {name}")


if __name__ == "__main__":
    serve(
        "需要 Base64、SHA-256 或一个新的 UUID 时，调用对应工具。不要用它处理密钥文件。",
        [
            {
                "name": "base64_encode",
                "description": "把文本按 UTF-8 编成 Base64。",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
            {
                "name": "base64_decode",
                "description": "把 Base64 解成 UTF-8 文本。",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
            {
                "name": "sha256",
                "description": "计算文本的 SHA-256 十六进制摘要。",
                "inputSchema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
            {
                "name": "new_uuid",
                "description": "生成一个随机 UUID。",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ],
        call,
    )
