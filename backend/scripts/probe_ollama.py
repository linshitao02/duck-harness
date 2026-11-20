import httpx

native = httpx.post(
    "http://127.0.0.1:11434/api/chat",
    json={
        "model": "qwen3:4b",
        "messages": [{"role": "user", "content": "只输出两个字：收到"}],
        "think": False,
        "stream": False,
    },
    timeout=90,
)
message = native.json().get("message", {})
print("native", native.status_code, repr(message.get("content", "")[:80]))

compat = httpx.post(
    "http://127.0.0.1:11434/v1/chat/completions",
    json={
        "model": "qwen3:4b",
        "messages": [{"role": "user", "content": "只输出两个字：收到"}],
        "think": False,
        "stream": False,
        "max_tokens": 32,
    },
    timeout=90,
)
print("compat", compat.status_code, compat.text[:240])
