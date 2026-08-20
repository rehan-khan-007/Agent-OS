import time

from httpx import AsyncClient

from app.config import settings
from app.observability.tracing import langfuse, is_enabled
from app.routing.router import route_model


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"


async def chat_completion(
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str | None = None,
) -> dict:
    """Call OpenRouter chat completions endpoint, traced via Langfuse."""
    if model is None:
        model = route_model(messages, tools)

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

    start = time.time()
    async with AsyncClient(timeout=60) as client:
        resp = await client.post(OPENROUTER_URL, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    duration = time.time() - start

    if is_enabled():
        try:
            with langfuse.start_as_current_observation(
                as_type="generation",
                name="chat_completion",
                model=model,
                input=messages,
                output=data.get("choices", [{}])[0].get("message"),
                metadata={"duration_seconds": duration, "had_tools": bool(tools)},
            ):
                pass
        except Exception:
            pass  # tracing failures should never break the actual agent call

    return data


def extract_choice(data: dict) -> dict:
    """Extract the first choice message from an API response."""
    return data["choices"][0]["message"]