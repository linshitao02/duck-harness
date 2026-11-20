# 公开价的估算，单位是美元 / 百万 token。Ollama 本地模型按 0 计。
RATES = {
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    if not model or not str(model).startswith("deepseek"):
        return 0.0
    input_rate, output_rate = RATES.get(model, (0.27, 1.10))
    return round((prompt_tokens * input_rate + completion_tokens * output_rate) / 1_000_000, 8)
