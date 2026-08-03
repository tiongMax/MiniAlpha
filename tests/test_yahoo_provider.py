"""Mapping tests for the Yahoo Finance adapter."""

import asyncio

import pytest

from app.domain.errors import FinancialProviderError, SymbolNotFoundError
from app.providers.yahoo import YahooFinanceProvider


class FakeTicker:
    """Minimal yfinance ticker double with controlled response dictionaries."""

    def __init__(
        self,
        _symbol: str,
        *,
        info: dict[str, object],
        fast_info: dict[str, object],
    ) -> None:
        """Initialize provider response data.

        Args:
            _symbol: Requested ticker; unused by this double.
            info: Detailed Yahoo-style response fields.
            fast_info: Lightweight Yahoo-style price fields.
        """
        self._info = info
        self.fast_info = fast_info

    def get_info(self) -> dict[str, object]:
        """Return the configured detailed response dictionary."""
        return self._info


def test_maps_provider_fields_and_preserves_zero(monkeypatch) -> None:
    """Verify Yahoo field mapping, unit normalization, and zero preservation.

    Args:
        monkeypatch: Pytest fixture used to replace ``yfinance.Ticker``.
    """
    info = {
        "symbol": "TEST",
        "longName": "Test Company",
        "fullExchangeName": "Test Exchange",
        "currency": "USD",
        "sector": "Technology",
        "industry": "Software",
        "trailingPE": 0,
        "forwardPE": 20,
        "priceToBook": 3,
        "totalRevenue": 1_000,
        "revenueGrowth": 0,
        "operatingMargins": 0.25,
        "profitMargins": 0.2,
        "totalCash": 100,
        "totalDebt": 50,
        "dividendYield": 2.5,
        "trailingAnnualDividendYield": 0.024,
        "beta": 1.1,
    }
    fast_info = {"last_price": 12.5, "market_cap": 5000}
    monkeypatch.setattr(
        "app.providers.yahoo.yf.Ticker",
        lambda symbol: FakeTicker(symbol, info=info, fast_info=fast_info),
    )

    result = YahooFinanceProvider()._fetch("TEST")

    assert result.company_name == "Test Company"
    assert result.price == 12.5
    assert result.market_cap == 5000
    assert result.trailing_pe == 0.0
    assert result.revenue_growth == 0.0
    assert result.dividend_yield == 0.024
    assert result.provider == "Yahoo Finance"


def test_treats_empty_response_as_unknown_symbol(monkeypatch) -> None:
    """Verify that an empty Yahoo response becomes a not-found error.

    Args:
        monkeypatch: Pytest fixture used to replace ``yfinance.Ticker``.
    """
    monkeypatch.setattr(
        "app.providers.yahoo.yf.Ticker",
        lambda symbol: FakeTicker(symbol, info={}, fast_info={}),
    )

    with pytest.raises(SymbolNotFoundError):
        YahooFinanceProvider()._fetch("UNKNOWN")


def test_converts_unexpected_upstream_failure(monkeypatch) -> None:
    """Verify that raw upstream details are wrapped by a domain-safe error.

    Args:
        monkeypatch: Pytest fixture used to replace ``yfinance.Ticker``.
    """

    def fail(_symbol):
        """Simulate an unexpected yfinance constructor failure.

        Args:
            _symbol: Requested ticker; unused.

        Raises:
            RuntimeError: Always, to exercise provider error translation.
        """
        raise RuntimeError("upstream detail")

    monkeypatch.setattr("app.providers.yahoo.yf.Ticker", fail)

    with pytest.raises(FinancialProviderError) as error:
        asyncio.run(YahooFinanceProvider().get_company_overview("AAPL"))

    assert "Yahoo Finance could not retrieve data for AAPL." in str(error.value)
    assert "upstream detail" not in str(error.value)
