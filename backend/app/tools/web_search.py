from httpx import AsyncClient

from app.config import settings
from app.tools.base import BaseTool, ToolResult

TAVILY_URL = "https://api.tavily.com/search"


class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the live web for current information — news, prices, dates, "
        "or anything that may have changed since training data cutoff. "
        "Returns a list of relevant results with titles, URLs, and short snippets."
    )

    async def run(self, query: str) -> ToolResult:
        if not settings.tavily_api_key:
            return ToolResult(output=None, error="Web search is not configured (missing API key).")

        body = {
            "api_key": settings.tavily_api_key,
            "query": query,
            "max_results": 5,
        }
        try:
            async with AsyncClient(timeout=20) as client:
                resp = await client.post(TAVILY_URL, json=body)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            return ToolResult(output=None, error=str(e))

        results = data.get("results", [])
        formatted = [
            {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content")}
            for r in results
        ]
        return ToolResult(output=formatted)

    def to_openai_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query",
                        }
                    },
                    "required": ["query"],
                },
            },
        }