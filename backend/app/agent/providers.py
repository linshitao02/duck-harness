import asyncio
import json
from dataclasses import dataclass, field

import httpx

# 还没吐出任何 token 时才重试。已经开始输出就停，避免把同一段文字再吐一遍。
# 408/409/429 和 5xx 最多 10 次。529 只给用户正在等的前台请求重试，连续 3 次就换备用模型或报错。
# 400/404/422 不重试，也不拿掉工具再请求一次。
BASE_DELAY = 0.5
MAX_RETRIES = 10
MAX_OVERLOADS = 3
FOREGROUND_SOURCES = {"foreground", "compact", "agent"}


class ModelFallback(RuntimeError):
    """前台请求连续过载，调用方应改用备用模型再请求一次。"""

    def __init__(self, original: str, fallback: str):
        super().__init__(f"原模型 {original} 连续过载，改用 {fallback}")
        self.original = original
        self.fallback = fallback


@dataclass
class StreamEvent:
    kind: str
    text: str = ""
    index: int = 0
    tool_id: str = ""
    tool_name: str = ""
    arguments_delta: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0


def _tool_event(index: int, slot: dict) -> StreamEvent:
    return StreamEvent(
        kind="tool_call",
        index=index,
        tool_id=slot.get("id") or f"call_{index}",
        tool_name=slot.get("name") or "",
        arguments_delta=slot.get("arguments") or "",
    )


@dataclass
class ScriptedProvider:
    """测试用模型：按预设顺序返回文本或工具调用。"""

    steps: list[dict]
    name: str = "scripted"
    calls: list[dict] = field(default_factory=list)
    index: int = 0

    async def stream(self, messages, tools, model, should_stop=None, max_tokens=None, query_source="foreground", fallback_model=""):
        del query_source, fallback_model
        names = []
        if tools:
            names = [item["function"]["name"] for item in tools]
        self.calls.append({"messages": messages, "tools": names, "model": model})
        step = self.steps[min(self.index, len(self.steps) - 1)]
        self.index += 1
        reasoning = step.get("reasoning") or ""
        if reasoning:
            yield StreamEvent(kind="reasoning", text=reasoning)
        text = step.get("content") or ""
        if text:
            mid = max(1, len(text) // 2)
            yield StreamEvent(kind="text", text=text[:mid])
            if should_stop and should_stop():
                return
            yield StreamEvent(kind="text", text=text[mid:])
        for position, call in enumerate(step.get("tool_calls") or []):
            raw = call.get("arguments", {})
            if not isinstance(raw, str):
                raw = json.dumps(raw, ensure_ascii=False)
            yield StreamEvent(
                kind="tool_call",
                index=position,
                tool_id=call.get("id") or f"call_{position}",
                tool_name=call.get("name") or "",
                arguments_delta=raw,
            )
        if step.get("finish"):
            yield StreamEvent(kind="finish", text=str(step["finish"]))
        yield StreamEvent(
            kind="usage",
            prompt_tokens=int(step.get("prompt_tokens", 12)),
            completion_tokens=int(step.get("completion_tokens", 6)),
        )


class OpenAICompatProvider:
    """DeepSeek 与 Ollama 都走兼容 OpenAI 的 chat completions。"""

    def __init__(self, base_url: str, api_key: str, name: str, transport=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.name = name
        self.transport = transport

    @property
    def endpoint(self) -> str:
        return self.base_url + "/chat/completions"

    def _client(self) -> httpx.AsyncClient:
        kwargs = {"timeout": httpx.Timeout(180, connect=5)}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return httpx.AsyncClient(**kwargs)

    async def stream(
        self,
        messages,
        tools,
        model,
        should_stop=None,
        max_tokens=None,
        query_source="foreground",
        fallback_model="",
    ):
        if self.name == "deepseek" and not self.api_key:
            raise RuntimeError("没有配置 DeepSeek API Key")
        delay = BASE_DELAY
        attempt = 0
        overloads = 0
        while True:
            started = False
            try:
                async for event in self._once(messages, tools, model, should_stop, max_tokens):
                    started = True
                    yield event
                return
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                detail = (exc.response.text or "")[:400]
                if started:
                    raise RuntimeError(f"模型请求失败 {status}: {detail}") from exc
                overloaded = status == 529 or "overloaded_error" in detail
                if overloaded:
                    if query_source not in FOREGROUND_SOURCES:
                        raise RuntimeError(f"模型过载 {status}: {detail}") from exc
                    overloads += 1
                    if overloads >= MAX_OVERLOADS:
                        if fallback_model and fallback_model != model:
                            raise ModelFallback(model, fallback_model) from exc
                        raise RuntimeError("模型连续过载（529），没有备用模型") from exc
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 32)
                    continue
                retryable = status in {408, 409, 429} or status >= 500
                attempt += 1
                if retryable and attempt < MAX_RETRIES:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 32)
                    continue
                raise RuntimeError(f"模型请求失败 {status}: {detail}") from exc
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if started:
                    raise RuntimeError(f"模型连接失败: {exc}") from exc
                attempt += 1
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 32)
                    continue
                raise RuntimeError(f"模型连接失败: {exc}") from exc

    async def _once(self, messages, tools, model, should_stop, max_tokens):
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "stream": True,
            "max_tokens": max_tokens or 4096,
        }
        if self.name == "deepseek":
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with self._client() as client:
            async with client.stream("POST", self.endpoint, headers=headers, json=payload) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise httpx.HTTPStatusError(
                        "model error",
                        request=response.request,
                        response=httpx.Response(response.status_code, content=body, request=response.request),
                    )
                tool_acc: dict[int, dict] = {}
                finish = ""
                async for line in response.aiter_lines():
                    if should_stop and should_stop():
                        break
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    usage = obj.get("usage") or {}
                    if usage:
                        details = usage.get("prompt_tokens_details") or {}
                        yield StreamEvent(
                            kind="usage",
                            prompt_tokens=int(usage.get("prompt_tokens") or 0),
                            completion_tokens=int(usage.get("completion_tokens") or 0),
                            cache_hit_tokens=int(
                                usage.get("prompt_cache_hit_tokens") or details.get("cached_tokens") or 0
                            ),
                            cache_miss_tokens=int(usage.get("prompt_cache_miss_tokens") or 0),
                        )
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    if choices[0].get("finish_reason"):
                        finish = choices[0]["finish_reason"]
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content") or ""
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    if reasoning:
                        yield StreamEvent(kind="reasoning", text=reasoning)
                    if content:
                        yield StreamEvent(kind="text", text=content)
                    for piece in delta.get("tool_calls") or []:
                        index = int(piece.get("index") or 0)
                        for older in sorted(tool_acc):
                            if older < index and not tool_acc[older].get("yielded") and tool_acc[older].get("name"):
                                slot = tool_acc[older]
                                slot["yielded"] = True
                                yield _tool_event(older, slot)
                        slot = tool_acc.setdefault(index, {"id": "", "name": "", "arguments": "", "yielded": False})
                        if piece.get("id"):
                            slot["id"] = piece["id"]
                        function = piece.get("function") or {}
                        if function.get("name"):
                            slot["name"] = function["name"]
                        if function.get("arguments"):
                            slot["arguments"] += function["arguments"]
                for index in sorted(tool_acc):
                    slot = tool_acc[index]
                    if slot.get("yielded") or not slot.get("name"):
                        continue
                    slot["yielded"] = True
                    yield _tool_event(index, slot)
                if finish:
                    yield StreamEvent(kind="finish", text=finish)


class Hub:
    def __init__(self):
        self.override = None

    def resolve(self, provider_name: str, db):
        if self.override is not None:
            return self.override
        from ..config import get_settings
        from ..services.runtime import all_settings, deepseek_credentials

        settings = get_settings()
        stored = all_settings(db)
        if provider_name == "ollama":
            base = (stored.get("ollama_base") or settings.ollama_base_url).rstrip("/")
            return OpenAICompatProvider(base + "/v1", "ollama", "ollama")
        base, key, _source = deepseek_credentials(db)
        return OpenAICompatProvider(base, key, "deepseek")


hub = Hub()


async def collect_text(provider, model: str, prompt: str, tools=None, max_tokens=128, query_source="background") -> str:
    parts: list[str] = []
    calls: list[str] = []
    async for event in provider.stream(
        [{"role": "user", "content": prompt}],
        tools,
        model,
        max_tokens=max_tokens,
        query_source=query_source,
    ):
        if event.kind in {"text", "reasoning"}:
            parts.append(event.text)
        elif event.kind == "tool_call":
            calls.append(event.tool_name)
    text = "".join(parts).strip()
    if calls:
        text = (text + "\n" if text else "") + "工具: " + ", ".join(calls)
    return text
