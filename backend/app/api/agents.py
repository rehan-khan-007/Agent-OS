import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from app.agents.graph import agent
from app.memory.short_term import memory

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentRequest(BaseModel):
    message: str
    session_id: str | None = None  # if omitted, a new session is created


class AgentResponse(BaseModel):
    response: str
    session_id: str
    messages: list[dict]


@router.post("/chat", response_model=AgentResponse)
async def chat(request: AgentRequest):
    session_id = request.session_id or str(uuid.uuid4())

    # Load prior history for this session, then append the new user message
    history = memory.get_history(session_id)
    user_message = {"role": "user", "content": request.message}

    state = {
        "messages": history + [user_message],
        "next": "",
    }
    result = await agent.ainvoke(state)
    messages = result["messages"]

    # Persist the full updated history (including this turn) back into memory
    memory.extend(session_id, messages[len(history):])

    last = messages[-1]["content"] if messages else ""
    return AgentResponse(response=last, session_id=session_id, messages=messages)