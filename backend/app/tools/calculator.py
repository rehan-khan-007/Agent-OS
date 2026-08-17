import math
from typing import Any

from app.tools.base import BaseTool, ToolResult


class CalculatorTool(BaseTool):
    name = "calculator"
    description = "Evaluate mathematical expressions. Supports +, -, *, /, **, sqrt, sin, cos."

    async def run(self, expression: str) -> ToolResult:
        try:
            allowed = {"x": 1}
            allowed.update({k: v for k, v in math.__dict__.items() if not k.startswith("_")})
            result = eval(expression, {"__builtins__": {}}, allowed)
            return ToolResult(output=round(result, 6))
        except Exception as e:
            return ToolResult(output=None, error=str(e))

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expression": {
                            "type": "string",
                            "description": "Math expression to evaluate",
                        }
                    },
                    "required": ["expression"],
                },
            },
        }