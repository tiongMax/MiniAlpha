"""Tests for the deterministic Phase 1 company tool."""

import asyncio

from app.agent.tools import get_company_overview


def invoke_company_tool(symbol: str) -> str:
    return asyncio.run(get_company_overview.ainvoke({"symbol": symbol}))


def test_returns_apple_sample_data() -> None:
    result = invoke_company_tool("AAPL")

    assert "Apple Inc. (AAPL)" in result
    assert "Operating margin: 31.7%" in result


def test_returns_microsoft_sample_data() -> None:
    result = invoke_company_tool("MSFT")

    assert "Microsoft Corporation (MSFT)" in result
    assert "Revenue growth: 15.2%" in result


def test_normalizes_lowercase_symbol() -> None:
    result = invoke_company_tool(" aapl ")

    assert "Apple Inc. (AAPL)" in result


def test_reports_unknown_symbol_without_crashing() -> None:
    result = invoke_company_tool("TSLA")

    assert "No Phase 1 sample data is available for TSLA" in result

