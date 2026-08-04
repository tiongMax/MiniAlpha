"""Tests for symbol normalization and provider delegation."""

import asyncio
from datetime import datetime, timezone

import pytest

from app.domain.company import CompanyOverview
from app.domain.errors import InvalidSymbolError
from app.services.company_research import CompanyResearchService, normalize_symbol


def make_overview(symbol: str) -> CompanyOverview:
    """Build a minimal normalized snapshot for service tests.

    Args:
        symbol: Ticker stored in the generated overview.

    Returns:
        Company overview whose optional financial fields are all missing.
    """
    return CompanyOverview(
        symbol=symbol,
        company_name=None,
        exchange=None,
        currency=None,
        sector=None,
        industry=None,
        price=None,
        market_cap=None,
        trailing_pe=None,
        forward_pe=None,
        price_to_book=None,
        total_revenue=None,
        revenue_growth=None,
        operating_margin=None,
        profit_margin=None,
        total_cash=None,
        total_debt=None,
        dividend_yield=None,
        beta=None,
        provider="Fake",
        retrieved_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )


class RecordingProvider:
    """Provider double that records the normalized delegated ticker.

    Attributes:
        requested_symbol: Most recent symbol passed by the service, or
            ``None`` before the first call.
    """

    def __init__(self) -> None:
        """Initialize an unused provider recording."""
        self.requested_symbol = None

    async def get_company_overview(self, symbol: str) -> CompanyOverview:
        """Record a delegated symbol and return a minimal snapshot.

        Args:
            symbol: Normalized ticker passed by the service.

        Returns:
            Minimal overview containing the same ticker.
        """
        self.requested_symbol = symbol
        return make_overview(symbol)


def test_service_normalizes_before_calling_provider() -> None:
    """Verify that provider delegation receives a trimmed uppercase symbol."""
    provider = RecordingProvider()
    service = CompanyResearchService(provider)

    result = asyncio.run(service.get_company_overview(" brk-b "))

    assert provider.requested_symbol == "BRK-B"
    assert result.symbol == "BRK-B"


@pytest.mark.parametrize("symbol", ["", "AAPL!", "two words", "A" * 21])
def test_rejects_invalid_symbols(symbol: str) -> None:
    """Verify local rejection of malformed ticker input.

    Args:
        symbol: Invalid ticker supplied by the parametrized test case.
    """
    with pytest.raises(InvalidSymbolError):
        normalize_symbol(symbol)
