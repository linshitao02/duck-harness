"""长度、质量和温度换算 MCP。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frame import serve

LENGTH = {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0}
MASS = {"mg": 0.001, "g": 1.0, "kg": 1000.0}


def _temperature(value: float, source: str, target: str) -> float:
    names = {"c": "c", "f": "f", "k": "k", "celsius": "c", "fahrenheit": "f", "kelvin": "k"}
    left = names.get(source.lower())
    right = names.get(target.lower())
    if left is None or right is None:
        raise ValueError("温度单位用 C、F 或 K")
    if left == "c":
        kelvin = value + 273.15
    elif left == "f":
        kelvin = (value - 32) * 5 / 9 + 273.15
    else:
        kelvin = value
    if right == "c":
        return kelvin - 273.15
    if right == "f":
        return (kelvin - 273.15) * 9 / 5 + 32
    return kelvin


def call(name: str, arguments: dict) -> str:
    if name != "convert_unit":
        raise ValueError(f"没有这个工具: {name}")
    try:
        value = float(arguments.get("value"))
    except (TypeError, ValueError) as exc:
        raise ValueError("value 要是数字") from exc
    source = str(arguments.get("from_unit") or "").strip()
    target = str(arguments.get("to_unit") or "").strip()
    if source in LENGTH and target in LENGTH:
        converted = value * LENGTH[source] / LENGTH[target]
    elif source in MASS and target in MASS:
        converted = value * MASS[source] / MASS[target]
    else:
        converted = _temperature(value, source, target)
    if float(converted).is_integer():
        return str(int(converted))
    return f"{converted:.6g}"


if __name__ == "__main__":
    serve(
        "需要换算长度、质量或温度时调用 convert_unit。长度用 mm、cm、m、km，质量用 mg、g、kg，温度用 C、F、K。",
        [
            {
                "name": "convert_unit",
                "description": "在同一类单位之间换算，例如 2 km 到 m，或 0 C 到 F。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "number"},
                        "from_unit": {"type": "string"},
                        "to_unit": {"type": "string"},
                    },
                    "required": ["value", "from_unit", "to_unit"],
                },
            }
        ],
        call,
    )
