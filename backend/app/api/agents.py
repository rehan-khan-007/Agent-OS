from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
import asyncio
import json
import uuid

from app.agents.graph import agent
from app.memory.long_term import memory
from app.database import get_session
from app.cache.redis_client import get_cached_response, cache_response
from app.ratelimit.limiter import rate_limit
from app.observability.tracing import langfuse, is_enabled

router = APIRouter(prefix="/agents", tags=["agents"])

# Each real chat turn costs at least one LLM call (more with tools),
# so this limit exists to prevent a single visitor from running up
# the OpenRouter bill via scripted/repeated requests to the public
# deployment.
CHAT_RATE_LIMIT = rate_limit("agents_chat", limit=20, window_seconds=300)


class AgentRequest(BaseModel):
    message: str
    session_id: str | None = None


class AgentResponse(BaseModel):
    response: str
    session_id: str
    messages: list[dict]
    cached: bool = False


async def _run_agent(request: AgentRequest, db: AsyncSession) -> tuple[str, list[dict], bool]:
    """Runs the agent for a request, using cache if an identical
    (session_id, message) pair was submitted within the idempotency
    window. Returns (session_id, messages, was_cached)."""
    session_id = request.session_id or str(uuid.uuid4())

    if request.session_id:
        cached = await get_cached_response(session_id, request.message)
        if cached is not None:
            return session_id, cached["messages"], True

    history = await memory.get_history(session_id, db)
    user_message = {"role": "user", "content": request.message}

    state = {"messages": history + [user_message], "next": ""}

    # Wraps the whole agent run in one parent Langfuse observation, so
    # the model's tool-selection call, the tool execution itself, and
    # the final response generation all nest under ONE connected trace
    # per request — without this, each of those was its own separate,
    # disconnected trace (this is genuine Langfuse SDK v3 behavior:
    # a start_as_current_observation call with no enclosing parent
    # always starts its own new trace), making it impossible to see a
    # full agent run as a single flow in the Langfuse UI.
    #
    # result is only ever assigned by calling agent.ainvoke() exactly
    # once below — the "if result is None" fallback exists so that a
    # tracing-only failure (setup, or span.update() after a
    # successful call) can never cause the real agent call to run
    # TWICE, which would double real cost and could double any
    # real side effects.
    result = None
    if is_enabled():
        try:
            with langfuse.start_as_current_observation(
                as_type="agent",
                name="agent_run",
                input=request.message,
                metadata={"session_id": session_id},
            ) as span:
                result = await agent.ainvoke(state)
                span.update(output=result["messages"][-1].get("content"))
        except Exception:
            pass  # tracing must never block the real call

    if result is None:
        result = await agent.ainvoke(state)

    messages = result["messages"]

    await memory.extend(session_id, messages[len(history):], db)
    await cache_response(session_id, request.message, {"messages": messages})

    return session_id, messages, False


@router.post("/chat", response_model=AgentResponse, dependencies=[Depends(CHAT_RATE_LIMIT)])
async def chat(request: AgentRequest, db: AsyncSession = Depends(get_session)):
    session_id, messages, was_cached = await _run_agent(request, db)
    last = messages[-1]["content"] if messages else ""
    return AgentResponse(response=last, session_id=session_id, messages=messages, cached=was_cached)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _extract_tool_calls(messages: list[dict]) -> list[dict]:
    """
    Pulls out every tool call the agent actually made this turn, from
    the already-computed message list — no extra agent work, just
    surfacing what already happened. Used to stream real tool-call
    events to the frontend (see chat_stream), so the UI can show what
    the agent is actually doing rather than just its final answer.
    """
    calls = []
    for msg in messages:
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            calls.append({"name": fn.get("name"), "arguments": fn.get("arguments")})
    return calls


@router.post("/chat/stream", dependencies=[Depends(CHAT_RATE_LIMIT)])
async def chat_stream(request: AgentRequest, db: AsyncSession = Depends(get_session)):
    session_id, messages, was_cached = await _run_agent(request, db)
    final_text = messages[-1]["content"] if messages else ""
    tool_calls = _extract_tool_calls(messages)

    async def event_stream():
        yield _sse({"type": "session", "session_id": session_id})

        for call in tool_calls:
            yield _sse({"type": "tool_call", "name": call["name"], "arguments": call["arguments"]})
            if not was_cached:
                await asyncio.sleep(0.15)  # brief pause so the trace is visibly readable, not a flash

        words = final_text.split(" ")
        for i, word in enumerate(words):
            piece = word + (" " if i < len(words) - 1 else "")
            yield _sse({"type": "chunk", "text": piece})
            if not was_cached:
                await asyncio.sleep(0.02)
        yield _sse({"type": "done", "cached": was_cached})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
