from abc import ABC, abstractmethod
from typing import Any


class ToolResult:
    def __init__(self, output: Any, error: str | None = None):
        self.output = output
        self.error = error

    @property
    def success(self) -> bool:
        return self.error is None


class BaseTool(ABC):
    name: str
    description: str

    @abstractmethod
    async def run(self, **kwargs) -> ToolResult:
        ...

    def to_openai_tool(self) -> dict:
        """Returns tool definition in OpenAI function-calling format."""
        raise NotImplementedError