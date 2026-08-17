from httpx import AsyncClient

from app.config import settings


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"


async def chat_completion(
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Call OpenRouter chat completions endpoint."""
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    async with AsyncClient(timeout=60) as client:
        resp = await client.post(OPENROUTER_URL, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()


def extract_choice(data: dict) -> dict:
    """Extract the first choice message from an API response."""
    return data["choices"][0]["message"]