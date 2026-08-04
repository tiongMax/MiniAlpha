"""Focused tests for the routing decision that controls the agent loop."""

from langchain_core.messages import AIMessage
from langgraph.graph import END

from app.agent.nodes import route_after_model


def test_routes_to_tools_when_model_requests_a_tool() -> None:
    """Verify that a model tool call selects the tool-execution node."""
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "get_company_overview",
                        "args": {"symbol": "AAPL"},
                        "type": "tool_call",
                    }
                ],
            )
        ]
    }

    assert route_after_model(state) == "tools"


def test_routes_to_end_for_a_final_answer() -> None:
    """Verify that a normal model response terminates the graph."""
    state = {
        "messages": [
            AIMessage(content="I can help with company research."),
        ]
    }

    assert route_after_model(state) == END
