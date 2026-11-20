"""只做算术的 MCP。不执行名字、函数或字符串。"""

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from frame import serve

_BINARIES = {
    ast.Add: lambda left, right: left + right,
    ast.Sub: lambda left, right: left - right,
    ast.Mult: lambda left, right: left * right,
    ast.Div: lambda left, right: left / right,
    ast.FloorDiv: lambda left, right: left // right,
    ast.Mod: lambda left, right: left % right,
    ast.Pow: lambda left, right: left ** right,
}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARIES:
        left = _eval(node.left)
        right = _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 8:
            raise ValueError("指数太大")
        return _BINARIES[type(node.op)](left, right)
    raise ValueError("只接受数字和 + - * / // % **")


def call(name: str, arguments: dict) -> str:
    if name != "calculate":
        raise ValueError(f"没有这个工具: {name}")
    expression = str(arguments.get("expression") or "").strip()
    if not expression:
        raise ValueError("需要 expression")
    value = _eval(ast.parse(expression, mode="eval"))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


if __name__ == "__main__":
    serve(
        "需要计算算术表达式时调用 calculate。只能写数字和 + - * / // % **，不要夹带单位或说明。",
        [
            {
                "name": "calculate",
                "description": "计算一个算术表达式，例如 (2+3)*4。",
                "inputSchema": {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
            }
        ],
        call,
    )
