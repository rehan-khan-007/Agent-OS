import logging
import time

from httpx import AsyncClient, HTTPStatusError, TimeoutException, ConnectError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from app.config import settings
from app.observability.tracing import langfuse, is_enabled
from app.observability.logging import get_logger
from app.routing.router import route_model


OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-4o-mini"

logger = get_logger(__name__)


def _is_retryable(exc: BaseException) -> bool:
    """
    Decides whether a failure is worth retrying.

    Retryable: network-level failures (timeout, connection error) and
    5xx server errors — these are transient by nature and a second
    attempt has a real chance of succeeding.

    NOT retryable: 4xx client errors (bad request, unauthorized, etc.)
    — these mean something is wrong with what we sent, and resending
    the exact same malformed request will just fail the exact same
    way every time. Retrying a 400 doesn't fix the request; it just
    delays the failure and wastes calls. This distinction exists
    because of a real bug found in this project: a corrupted message
    history caused OpenRouter to return 400 Bad Request, and blind
    retries would have masked that as a flaky-looking failure instead
    of surfacing the actual data problem.
    """
    if isinstance(exc, (TimeoutException, ConnectError)):
        return True
    if isinstance(exc, HTTPStatusError):
        return 500 <= exc.response.status_code < 600
    return False


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    reraise=True,
    before_sleep=before_sleep_log(logger, logging.WARNING),
)
async def _call_openrouter(body: dict, headers: dict) -> dict:
    async with AsyncClient(timeout=30) as client:
        resp = await client.post(OPENROUTER_URL, json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def chat_completion(
    messages: list[dict],
    tools: list[dict] | None = None,
    model: str | None = None,
) -> dict:
    """Call OpenRouter chat completions endpoint, traced via Langfuse.

    Transient failures (timeouts, connection errors, 5xx) are retried
    up to 3 times with exponential backoff. Client errors (4xx) are
    never retried — see _is_retryable for why."""
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
    try:
        data = await _call_openrouter(body, headers)
    except HTTPStatusError as e:
        logger.error(
            "OpenRouter request failed (non-retryable or retries exhausted)",
            extra={"extra_fields": {
                "status_code": e.response.status_code,
                "model": model,
            }},
        )
        raise
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
