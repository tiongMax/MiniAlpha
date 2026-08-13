"""Credential-free contracts for request-scoped financial tool routing."""

from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from app.agent.intent_router import IntentRouter
from app.agent.tool_registry import ToolRegistry


async def _stub(**_arguments: object) -> str:
    return "synthetic result"


def _tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(
        coroutine=_stub,
        name=name,
        description=f"Synthetic {name} tool.",
    )


def _router() -> IntentRouter:
    return IntentRouter(
        ToolRegistry(
            [
                _tool("get_company_overview"),
                _tool("get_fundamental_ratios"),
                _tool("get_analyst_estimates"),
                _tool("get_company_news"),
                _tool("calculate_return_statistics"),
                _tool("calculate_volatility"),
                _tool("analyze_drawdowns"),
            ]
        )
    )


def test_routes_single_intent_to_registered_subset() -> None:
    route = _router().route(
        {"messages": [HumanMessage(content="Show Apple's recent news.")]}
    )

    assert route.intents == ("company_news",)
    assert route.selected_tool_names == ("get_company_news",)
    assert route.mode == "intent"


def test_routes_multi_intent_to_stable_union() -> None:
    route = _router().route(
        {
            "messages": [
                HumanMessage(
                    content="Review MSFT valuation ratios, news, returns, and risk."
                )
            ]
        }
    )

    assert route.intents == (
        "fundamental_ratios",
        "company_news",
        "return_statistics",
        "volatility",
    )
    assert route.selected_tool_names == (
        "get_fundamental_ratios",
        "get_company_news",
        "calculate_return_statistics",
        "calculate_volatility",
    )


def test_conceptual_question_exposes_no_tools() -> None:
    route = _router().route(
        {"messages": [HumanMessage(content="Explain what volatility means.")]}
    )

    assert route.selected_tool_names == ()
    assert route.mode == "no_tools"

    generic_market_concept = _router().route(
        {"messages": [HumanMessage(content="Define market capitalization.")]}
    )
    assert generic_market_concept.mode == "no_tools"


def test_conceptual_question_with_ticker_routes_current_data_tool() -> None:
    route = _router().route(
        {"messages": [HumanMessage(content="What is MSFT volatility?")]}
    )

    assert route.selected_tool_names == ("calculate_volatility",)
    assert route.mode == "intent"


def test_unknown_or_ambiguous_request_falls_back_to_all_tools() -> None:
    registry = ToolRegistry([_tool("get_company_overview"), _tool("get_company_news")])
    route = IntentRouter(registry).route(
        {"messages": [HumanMessage(content="Tell me about ACME's moat.")]}
    )

    assert route.selected_tool_names == registry.names
    assert route.mode == "fallback_all"


def test_latest_checkpointed_user_turn_is_rerouted() -> None:
    route = _router().route(
        {
            "messages": [
                HumanMessage(content="Show Apple news."),
                HumanMessage(content="Instead, calculate its return."),
            ]
        }
    )

    assert route.intents == ("return_statistics",)
    assert "get_company_news" not in route.selected_tool_names


def test_registry_rejects_unknown_tool_names() -> None:
    registry = ToolRegistry([_tool("get_company_overview")])

    try:
        registry.resolve(["not_registered"])
    except ValueError as error:
        assert "not_registered" in str(error)
    else:
        raise AssertionError("Expected an unknown tool-name failure.")
