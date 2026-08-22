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

router = APIRouter(prefix="/agents", tags=["agents"])


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
    result = await agent.ainvoke(state)
    messages = result["messages"]

    await memory.extend(session_id, messages[len(history):], db)
    await cache_response(session_id, request.message, {"messages": messages})

    return session_id, messages, False


@router.post("/chat", response_model=AgentResponse)
async def chat(request: AgentRequest, db: AsyncSession = Depends(get_session)):
    session_id, messages, was_cached = await _run_agent(request, db)
    last = messages[-1]["content"] if messages else ""
    return AgentResponse(response=last, session_id=session_id, messages=messages, cached=was_cached)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.post("/chat/stream")
async def chat_stream(request: AgentRequest, db: AsyncSession = Depends(get_session)):
    session_id, messages, was_cached = await _run_agent(request, db)
    final_text = messages[-1]["content"] if messages else ""

    async def event_stream():
        yield _sse({"type": "session", "session_id": session_id})
        words = final_text.split(" ")
        for i, word in enumerate(words):
            piece = word + (" " if i < len(words) - 1 else "")
            yield _sse({"type": "chunk", "text": piece})
            if not was_cached:
                await asyncio.sleep(0.02)
        yield _sse({"type": "done", "cached": was_cached})

    return StreamingResponse(event_stream(), media_type="text/event-stream")
