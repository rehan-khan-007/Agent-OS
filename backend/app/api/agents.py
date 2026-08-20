from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

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
    import uuid
    session_id = request.session_id or str(uuid.uuid4())

    history = await memory.get_history(session_id, db)
    user_message = {"role": "user", "content": request.message}

    state = {
        "messages": history + [user_message],
        "next": "",
    }
    result = await agent.ainvoke(state)
    messages = result["messages"]

    await memory.extend(session_id, messages[len(history):], db)

    last = messages[-1]["content"] if messages else ""
    return AgentResponse(response=last, session_id=session_id, messages=messages)