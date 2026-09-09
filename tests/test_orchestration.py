"""Focused tests for deterministic tool-orchestration contracts."""

import asyncio
from typing import cast

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import tool

from app.agent.graph import build_graph
from app.agent.orchestration import TOOL_CAPABILITIES, validate_tool_arguments
from app.agent.tools import create_default_tools

EXPECTED_TOOLS = {
    "get_company_overview",
    "get_price_history",
    "get_financial_statements",
    "get_fundamental_ratios",
    "get_analyst_estimates",
    "get_sec_filings",
    "get_ownership",
    "get_insider_activity",
    "get_company_news",
    "compare_companies",
    "calculate_return_statistics",
    "calculate_volatility",
    "analyze_drawdowns",
    "calculate_correlations",
    "calculate_technical_indicators",
    "backtest_moving_average",
}


def test_registry_covers_every_production_tool() -> None:
    """Keep the application registry synchronized with bound tool names."""
    assert set(TOOL_CAPABILITIES) == EXPECTED_TOOLS
    assert {item.name for item in create_default_tools()} == EXPECTED_TOOLS
    for capability in TOOL_CAPABILITIES.values():
        assert capability.purpose
        assert capability.exclusions
        assert capability.required_arguments
        assert capability.output_artifact_type
        assert capability.known_failure_modes
        assert capability.recovery_options
        assert capability.description_version == "orchestration-v1"


def test_validation_normalizes_safe_aliases_before_execution() -> None:
    result = validate_tool_arguments(
        "calculate_correlations",
        {
            "symbols": [" aapl ", "MSFT", "AAPL"],
            "period": "1Y",
            "interval": "1D",
        },
    )

    assert result.is_valid
    assert dict(result.arguments) == {
        "symbols": ["AAPL", "MSFT"],
        "period": "1y",
        "interval": "1d",
    }


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_code"),
    [
        ("get_company_overview", {}, "missing_argument"),
        ("get_company_overview", {"symbol": "not a ticker!"}, "invalid_symbol"),
        (
            "get_price_history",
            {"symbol": "AAPL", "period": "10y"},
            "invalid_period",
        ),
        (
            "get_financial_statements",
            {"symbol": "AAPL", "frequency": "monthly"},
            "invalid_frequency",
        ),
        (
            "compare_companies",
            {"symbols": ["AAPL", "aapl"]},
            "symbol_count",
        ),
        (
            "calculate_technical_indicators",
            {"symbol": "AAPL", "short_window": 50, "long_window": 20},
            "invalid_windows",
        ),
        (
            "backtest_moving_average",
            {"symbol": "AAPL", "transaction_cost_bps": -1},
            "invalid_transaction_cost",
        ),
    ],
)
def test_validation_rejects_known_deterministic_errors(
    tool_name: str,
    arguments: dict[str, object],
    expected_code: str,
) -> None:
    result = validate_tool_arguments(tool_name, arguments)

    assert not result.is_valid
    assert expected_code in {issue.code for issue in result.issues}


class TwoCallModel:
    """Request one invalid and one valid call, then finish."""

    def bind_tools(self, _tools):
        async def respond(messages):
            if isinstance(messages[-1], ToolMessage):
                tool_messages = [
                    message for message in messages if isinstance(message, ToolMessage)
                ]
                assert [message.tool_call_id for message in tool_messages] == [
                    "invalid-call",
                    "valid-call",
                ]
                return AIMessage(content="Completed with one rejected call.")
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "invalid-call",
                        "name": "get_price_history",
                        "args": {"symbol": "AAPL", "period": "10y"},
                        "type": "tool_call",
                    },
                    {
                        "id": "valid-call",
                        "name": "get_price_history",
                        "args": {
                            "symbol": " aapl ",
                            "period": "1Y",
                            "interval": "1D",
                        },
                        "type": "tool_call",
                    },
                ],
            )

        return RunnableLambda(respond)


def test_graph_rejects_invalid_call_and_executes_normalized_ready_call() -> None:
    """Validation prevents provider work without blocking independent calls."""
    received: list[tuple[str, str, str]] = []

    @tool(response_format="content_and_artifact")
    async def get_price_history(
        symbol: str, period: str = "6mo", interval: str = "1d"
    ) -> tuple[str, dict[str, object]]:
        """Return a test price artifact."""
        received.append((symbol, period, interval))
        return "prices", {
            "artifact_type": "price_history",
            "schema_version": 1,
            "status": "ok",
        }

    graph = build_graph(
        cast(BaseChatModel, TwoCallModel()),
        tools=[get_price_history],
    )
    result = asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="Compare two price windows.")]},
            config={"recursion_limit": 8},
        )
    )

    tool_messages = [
        message for message in result["messages"] if isinstance(message, ToolMessage)
    ]
    assert received == [("AAPL", "1y", "1d")]
    assert tool_messages[0].status == "error"
    assert tool_messages[0].artifact["validation_issues"][0]["code"] == (
        "invalid_period"
    )
    assert tool_messages[1].artifact["status"] == "ok"
    assert result["messages"][-1].content == "Completed with one rejected call."
