from typing import Any

from langgraph.graph import StateGraph, END
from typing import TypedDict, Literal, Sequence

from app.tools.base import BaseTool, ToolResult
from app.tools.calculator import CalculatorTool


# ── State ──────────────────────────────────────────────────

class AgentState(TypedDict):
    messages: list[dict]
    next: str


# ── Tools registry ─────────────────────────────────────────

TOOLS: dict[str, BaseTool] = {
    "calculator": CalculatorTool(),
}


def _format_tools_for_llm() -> str:
    lines = []
    for t in TOOLS.values():
        lines.append(f"- {t.name}: {t.description}")
    return "\n".join(lines)


# ── Nodes ──────────────────────────────────────────────────

def call_model(state: AgentState) -> dict:
    """LLM decides: respond directly or call a tool."""
    messages = state["messages"]
    last = messages[-1]["content"] if messages else ""

    # Simple routing logic — in production this would call an LLM
    # For now, check if last message looks like a math expression
    if any(op in last for op in ["+", "-", "*", "/", "**", "sqrt", "sin", "cos"]):
        return {"messages": messages, "next": "tools"}

    return {"messages": messages, "next": "respond"}


async def call_tool(state: AgentState) -> dict:
    """Execute the requested tool."""
    messages = state["messages"]
    last = messages[-1]["content"] if messages else ""

    # Simple heuristic: try calculator
    tool = TOOLS.get("calculator")
    if tool:
        result = await tool.run(expression=last)

        if result.success:
            messages.append({"role": "tool", "content": str(result.output), "tool": "calculator"})
        else:
            messages.append({"role": "tool", "content": f"Error: {result.error}", "tool": "calculator"})
    else:
        messages.append({"role": "tool", "content": "No tool available", "tool": "calculator"})

    return {"messages": messages, "next": "respond"}


def respond(state: AgentState) -> dict:
    """Generate final response."""
    messages = state["messages"]
    # In production this would call the LLM
    # For now, return the last tool output or a default message
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    if tool_msgs:
        response = tool_msgs[-1]["content"]
    else:
        response = "I understand your request. (LLM integration pending — drop in your model client.)"

    messages.append({"role": "assistant", "content": response})
    return {"messages": messages, "next": END}


# ── Router ─────────────────────────────────────────────────

def router(state: AgentState) -> str:
    return state.get("next", END)


# ── Build graph ────────────────────────────────────────────

def build_agent() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("model", call_model)
    workflow.add_node("tools", call_tool)
    workflow.add_node("respond", respond)

    workflow.set_entry_point("model")
    workflow.add_conditional_edges("model", router)
    workflow.add_edge("tools", "respond")
    workflow.add_edge("respond", END)

    return workflow.compile()


# Singleton
agent = build_agent()