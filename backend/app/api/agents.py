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

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentRequest(BaseModel):
    message: str
    session_id: str | None = None


class AgentResponse(BaseModel):
    response: str
    session_id: str
    messages: list[dict]


@router.post("/chat", response_model=AgentResponse)
async def chat(request: AgentRequest, db: AsyncSession = Depends(get_session)):
    session_id = request.session_id or str(uuid.uuid4())

    history = await memory.get_history(session_id, db)
    user_message = {"role": "user", "content": request.message}

    state = {"messages": history + [user_message], "next": ""}
    result = await agent.ainvoke(state)
    messages = result["messages"]

    await memory.extend(session_id, messages[len(history):], db)

    last = messages[-1]["content"] if messages else ""
    return AgentResponse(response=last, session_id=session_id, messages=messages)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


@router.post("/chat/stream")
async def chat_stream(request: AgentRequest, db: AsyncSession = Depends(get_session)):
    session_id = request.session_id or str(uuid.uuid4())

    history = await memory.get_history(session_id, db)
    user_message = {"role": "user", "content": request.message}

    state = {"messages": history + [user_message], "next": ""}
    result = await agent.ainvoke(state)
    messages = result["messages"]

    await memory.extend(session_id, messages[len(history):], db)

    final_text = messages[-1]["content"] if messages else ""

    async def event_stream():
        yield _sse({"type": "session", "session_id": session_id})
        words = final_text.split(" ")
        for i, word in enumerate(words):
            piece = word + (" " if i < len(words) - 1 else "")
            yield _sse({"type": "chunk", "text": piece})
            await asyncio.sleep(0.02)
        yield _sse({"type": "done"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")