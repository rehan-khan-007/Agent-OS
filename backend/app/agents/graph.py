from typing import Any

from langgraph.graph import StateGraph, END
from typing import TypedDict

from app.tools.base import BaseTool, ToolResult
from app.tools.calculator import CalculatorTool
from app.tools.retrieve import RetrieveTool
from app.llm.client import chat_completion, extract_choice


# ── State ──────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: list[dict]
    next: str


# ── Tools registry ─────────────────────────────────────────

TOOLS: dict[str, BaseTool] = {
    "calculator": CalculatorTool(),
    "retrieve": RetrieveTool(),
}


def _get_openai_tools() -> list[dict]:
    return [t.to_openai_tool() for t in TOOLS.values()]


async def _run_tool_call(tool_name: str, args: dict) -> ToolResult:
    tool = TOOLS.get(tool_name)
    if tool is None:
        return ToolResult(output=None, error=f"Unknown tool: {tool_name}")
    return await tool.run(**args)


# ── Nodes ──────────────────────────────────────────────────

async def call_model(state: AgentState) -> dict:
    """LLM decides: respond directly or call a tool."""
    msg = await chat_completion(
        messages=state["messages"],
        tools=_get_openai_tools(),
    )
    choice = extract_choice(msg)

    messages = list(state["messages"])
    messages.append(choice)

    has_tool_calls = "tool_calls" in choice and choice["tool_calls"]
    return {"messages": messages, "next": "tools" if has_tool_calls else "respond"}


async def call_tool(state: AgentState) -> dict:
    """Execute the tool the LLM requested."""
    messages = list(state["messages"])
    last = messages[-1]
    results = []

    for tc in last.get("tool_calls", []):
        fn = tc["function"]
        name, args_str = fn["name"], fn["arguments"]
        import json
        args = json.loads(args_str)
        result = await _run_tool_call(name, args)
        results.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": str(result.output) if result.success else f"Error: {result.error}",
        })

    messages.extend(results)
    return {"messages": messages, "next": "respond"}


async def respond(state: AgentState) -> dict:
    """Generate final response from LLM with tool results."""
    msg = await chat_completion(messages=state["messages"])
    choice = extract_choice(msg)

    messages = list(state["messages"])
    messages.append(choice)
    return {"messages": messages, "next": END}


# ── Router ─────────────────────────────────────────────────

def router(state: AgentState) -> str:
    return state.get("next", END)


# ── Build graph ────────────────────────────────────────────

def build_agent():
    workflow = StateGraph(AgentState)

    workflow.add_node("model", call_model)
    workflow.add_node("tools", call_tool)
    workflow.add_node("respond", respond)

    workflow.set_entry_point("model")
    workflow.add_conditional_edges("model", router, {"tools": "tools", "respond": END})
    workflow.add_edge("tools", "respond")
    workflow.add_edge("respond", END)

    return workflow.compile()


# Singleton
agent = build_agent()