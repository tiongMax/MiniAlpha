"""Deterministic watchlist market-data service tests."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.errors import SymbolNotFoundError
from app.domain.prices import PriceHistory, PricePoint
from app.services.company_research import CompanyResearchService
from app.services.watchlist_market import WatchlistMarketService


def history(symbol: str, prices: list[float]) -> PriceHistory:
    """Build a small normalized daily history."""
    start = datetime(2026, 9, 1, tzinfo=UTC)
    return PriceHistory(
        symbol=symbol,
        currency="USD",
        period="3mo",
        interval="1d",
        points=tuple(
            PricePoint(
                timestamp=start + timedelta(days=index),
                open=price,
                high=price,
                low=price,
                close=price,
                adjusted_close=price,
                volume=100,
            )
            for index, price in enumerate(prices)
        ),
        provider="Fixture Finance",
        retrieved_at=datetime(2026, 9, 9, tzinfo=UTC),
    )


class PriceProvider:
    """Provider double with a controlled missing ticker."""

    async def get_price_history(
        self, symbol: str, *, period: str, interval: str
    ) -> PriceHistory:
        assert (period, interval) == ("3mo", "1d")
        if symbol == "MISSING":
            raise SymbolNotFoundError("missing")
        return history(symbol, [10.0, 12.0, 9.0, 11.0])


def test_batch_preserves_order_deduplicates_and_calculates_risk() -> None:
    """The compact response is deterministic and de-duplicates tickers."""
    service = WatchlistMarketService(CompanyResearchService(PriceProvider()))  # type: ignore[arg-type]

    items = asyncio.run(service.get_snapshots(["msft", "AAPL", "MSFT"]))

    assert [item.symbol for item in items] == ["MSFT", "AAPL"]
    first = items[0]
    assert first.status == "ok"
    assert first.currency == "USD"
    assert first.latest_price == 11.0
    assert first.previous_close == 9.0
    assert first.daily_change == 2.0
    assert first.daily_change_percent == pytest.approx(2 / 9)
    assert first.maximum_drawdown_3m == pytest.approx(-0.25)
    assert first.annualized_volatility_30d is not None


def test_batch_isolates_symbol_failures() -> None:
    """A missing symbol does not discard neighboring market snapshots."""
    service = WatchlistMarketService(CompanyResearchService(PriceProvider()))  # type: ignore[arg-type]

    items = asyncio.run(service.get_snapshots(["MISSING", "AAPL"]))

    assert items[0].status == "error"
    assert items[0].code == "symbol_not_found"
    assert items[1].status == "ok"
