"""Tests for the provider-neutral company overview tool."""

import asyncio
from datetime import UTC, datetime

from langchain_core.messages import ToolMessage

from app.agent.tools import create_company_overview_tool, format_company_overview
from app.domain.company import CompanyOverview
from app.domain.errors import SymbolNotFoundError


def make_overview(**overrides) -> CompanyOverview:
    """Build a complete company snapshot with optional field overrides.

    Args:
        **overrides: Dataclass field values that replace test defaults.

    Returns:
        Deterministic company overview for tool-formatting tests.
    """
    values = {
        "symbol": "AAPL",
        "company_name": "Apple Inc.",
        "exchange": "NasdaqGS",
        "currency": "USD",
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "price": 220.0,
        "market_cap": 3_300_000_000_000.0,
        "trailing_pe": 30.0,
        "forward_pe": 28.0,
        "price_to_book": 45.0,
        "total_revenue": 400_000_000_000.0,
        "revenue_growth": 0.064,
        "operating_margin": 0.317,
        "profit_margin": 0.25,
        "total_cash": 60_000_000_000.0,
        "total_debt": 100_000_000_000.0,
        "dividend_yield": 0.004,
        "beta": 1.2,
        "provider": "Fake Finance",
        "retrieved_at": datetime(2026, 8, 3, tzinfo=UTC),
    }
    values.update(overrides)
    return CompanyOverview(**values)


class SuccessfulService:
    """Service double that always returns a populated Apple snapshot."""

    async def get_company_overview(self, _symbol: str) -> CompanyOverview:
        """Return deterministic company data.

        Args:
            _symbol: Requested ticker; intentionally ignored.

        Returns:
            Populated Apple overview.
        """
        return make_overview()


class MissingService:
    """Service double that reports every ticker as unavailable."""

    async def get_company_overview(self, symbol: str) -> CompanyOverview:
        """Raise a controlled not-found error.

        Args:
            symbol: Requested ticker included in the error message.

        Raises:
            SymbolNotFoundError: Always.
        """
        raise SymbolNotFoundError(f"No company data is available for {symbol}.")


def invoke_as_tool_call(tool, symbol: str) -> ToolMessage:
    """Invoke a tool with LangChain's full tool-call envelope.

    Args:
        tool: LangChain tool to invoke asynchronously.
        symbol: Ticker placed in the tool arguments.

    Returns:
        Tool message containing both content and artifact fields.
    """
    result = asyncio.run(
        tool.ainvoke(
            {
                "type": "tool_call",
                "id": "call-company",
                "name": "get_company_overview",
                "args": {"symbol": symbol},
            }
        )
    )
    assert isinstance(result, ToolMessage)
    return result


def test_returns_compact_content_and_structured_artifact() -> None:
    """Verify successful tool text and versioned raw-data artifact output."""
    company_tool = create_company_overview_tool(SuccessfulService())

    result = invoke_as_tool_call(company_tool, "AAPL")

    assert "Apple Inc. (AAPL)" in result.content
    assert "Operating margin: 31.7%" in result.content
    assert "Dividend yield: 0.40%" in result.content
    assert "Source: Fake Finance" in result.content
    assert result.artifact["artifact_type"] == "company_overview"
    assert result.artifact["schema_version"] == 1
    assert result.artifact["data"]["market_cap"] == 3_300_000_000_000.0
    assert result.artifact["data"]["retrieved_at"] == "2026-08-03T00:00:00+00:00"


def test_returns_controlled_error_as_tool_result() -> None:
    """Verify expected service failures become error tool artifacts."""
    company_tool = create_company_overview_tool(MissingService())

    result = invoke_as_tool_call(company_tool, "UNKNOWN")

    assert "No company data is available for UNKNOWN." in result.content
    assert result.artifact["status"] == "error"


def test_formatter_distinguishes_missing_values_from_zero() -> None:
    """Verify that legitimate zeros are not formatted as missing values."""
    overview = make_overview(
        price=0.0,
        trailing_pe=None,
        revenue_growth=0.0,
    )

    result = format_company_overview(overview)

    assert "Price: USD 0.00" in result
    assert "Trailing / forward P/E: N/A / 28.00" in result
    assert "Revenue growth: 0.0%" in result
