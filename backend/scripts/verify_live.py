"""真实调用 DeepSeek 和本机 Ollama，并让 DeepSeek 走一轮工具。"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("AUTO_SEED", "false")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
os.environ["DATABASE_URL"] = (
    "mysql+pymysql://harness:harness_local@127.0.0.1:3306/harness_test?charset=utf8mb4"
)

from app.agent.loop import iter_events
from app.agent.providers import OpenAICompatProvider, collect_text
from app.config import get_settings
from app.db import init_db, open_session
from app.models import Conversation
from app.services.runtime import ensure_defaults


async def main():
    settings = get_settings()
    report = {"deepseek_chat": None, "ollama_chat": None, "deepseek_tool": None}
    deepseek = OpenAICompatProvider(settings.deepseek_base_url, settings.deepseek_api_key, "deepseek")
    try:
        report["deepseek_chat"] = await collect_text(
            deepseek, "deepseek-chat", "只回复两个字：收到。不要调用工具。", None, max_tokens=32
        )
    except Exception as exc:
        report["deepseek_chat"] = f"失败: {exc}"

    ollama = OpenAICompatProvider(settings.ollama_base_url.rstrip("/") + "/v1", "ollama", "ollama")
    ollama_text = ""
    for model in ("qwen3:4b", "qwen3.5:4b", "qwen2.5vl:3b"):
        try:
            ollama_text = await collect_text(ollama, model, "只输出两个字：收到 /no_think", None, max_tokens=32)
            report["ollama_chat"] = {"model": model, "text": ollama_text}
            if ollama_text.strip():
                break
        except Exception as exc:
            report["ollama_chat"] = {"model": model, "text": f"失败: {exc}"}
    report["deepseek_tool"] = await tool_round(settings)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    out = BACKEND.parent / "docs" / "live-models.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = (
        isinstance(report["deepseek_chat"], str)
        and "收到" in report["deepseek_chat"]
        and isinstance(report["ollama_chat"], dict)
        and "收到" in str(report["ollama_chat"].get("text", ""))
        and report["deepseek_tool"].get("ok")
    )
    return 0 if ok else 1


async def tool_round(settings):
    init_db()
    workspace = Path(tempfile.mkdtemp(prefix="harness-live-"))
    db = open_session()
    try:
        ensure_defaults(db)
        conversation = Conversation(
            title="live-tool",
            provider="deepseek",
            model="deepseek-chat",
            permission_mode="auto",
            workspace=str(workspace),
        )
        db.add(conversation)
        db.commit()
        events = []
        async for event in iter_events(
            db,
            conversation,
            "请调用 write_file，把 hello-live.txt 的内容写成 harness-ok。写完后只用一句话确认。",
            [],
        ):
            events.append(event["type"])
        matches = list(workspace.rglob("hello-live.txt"))
        content = matches[0].read_text(encoding="utf-8") if matches else ""
        return {
            "ok": "harness-ok" in content and "tool_start" in events,
            "events": events,
            "file": content,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
