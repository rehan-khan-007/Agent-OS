from fastapi import APIRouter
from pydantic import BaseModel

from app.agents.graph import agent

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentRequest(BaseModel):
    message: str


class AgentResponse(BaseModel):
    response: str
    messages: list[dict]


@router.post("/chat", response_model=AgentResponse)
async def chat(request: AgentRequest):
    state = {
        "messages": [{"role": "user", "content": request.message}],
        "next": "",
    }
    result = await agent.ainvoke(state)
    messages = result["messages"]
    last = messages[-1]["content"] if messages else ""
    return AgentResponse(response=last, messages=messages)