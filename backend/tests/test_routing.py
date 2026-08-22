"""Tests for the model routing policy — pure logic, no external dependencies."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routing.router import route_model
from app.routing.policies import MODEL_TIERS


def test_no_tools_routes_to_fast_tier():
    messages = [{"role": "user", "content": "hello"}]
    result = route_model(messages, tools=None)
    assert result == MODEL_TIERS["fast"]


def test_tools_but_short_history_routes_to_fast_tier():
    messages = [{"role": "user", "content": "hello"}]
    tools = [{"type": "function", "function": {"name": "calculator"}}]
    result = route_model(messages, tools=tools)
    assert result == MODEL_TIERS["fast"]


def test_tools_and_long_history_routes_to_strong_tier():
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "do something complex"},
    ]
    tools = [{"type": "function", "function": {"name": "calculator"}}]
    result = route_model(messages, tools=tools)
    assert result == MODEL_TIERS["strong"]


def test_long_history_without_tools_routes_to_fast_tier():
    # Long conversation but no tools available — should stay on fast tier,
    # since the "strong" trigger requires both conditions together.
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "another message"},
    ]
    result = route_model(messages, tools=None)
    assert result == MODEL_TIERS["fast"]


def test_empty_tools_list_treated_as_no_tools():
    messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    result = route_model(messages, tools=[])
    assert result == MODEL_TIERS["fast"]
