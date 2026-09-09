"""Batch watchlist market-data orchestration."""

import asyncio
from math import sqrt
from statistics import stdev

from app.domain.errors import FinancialDataError, SymbolNotFoundError
from app.domain.market import WatchlistMarketFailure, WatchlistMarketSnapshot
from app.domain.prices import PriceHistory
from app.services.company_research import CompanyResearchService, normalize_symbol


def _analysis_prices(history: PriceHistory) -> list[float]:
    """Use adjusted closes only when the complete window provides them."""
    if all(point.adjusted_close is not None for point in history.points):
        return [float(point.adjusted_close) for point in history.points]
    return [point.close for point in history.points]


def _maximum_drawdown(prices: list[float]) -> float:
    peak = prices[0]
    worst = 0.0
    for price in prices:
        peak = max(peak, price)
        worst = min(worst, price / peak - 1)
    return worst


class WatchlistMarketService:
    """Fetch watchlist histories concurrently and calculate compact metrics."""

    def __init__(
        self,
        companies: CompanyResearchService,
        *,
        concurrency: int = 8,
    ) -> None:
        self._companies = companies
        self._concurrency = concurrency

    async def get_snapshots(
        self, symbols: list[str]
    ) -> tuple[WatchlistMarketSnapshot | WatchlistMarketFailure, ...]:
        """Return ordered, isolated results for a normalized symbol batch."""
        normalized = list(dict.fromkeys(normalize_symbol(symbol) for symbol in symbols))
        semaphore = asyncio.Semaphore(self._concurrency)

        async def fetch(symbol: str):
            async with semaphore:
                try:
                    history = await self._companies.get_price_history(
                        symbol,
                        period="3mo",
                        interval="1d",
                    )
                    return self._snapshot(history)
                except SymbolNotFoundError:
                    return WatchlistMarketFailure(
                        symbol=symbol,
                        code="symbol_not_found",
                        message=f"No market data was found for {symbol}.",
                    )
                except FinancialDataError:
                    return WatchlistMarketFailure(
                        symbol=symbol,
                        code="provider_unavailable",
                        message=f"Market data for {symbol} is temporarily unavailable.",
                    )

        return tuple(await asyncio.gather(*(fetch(symbol) for symbol in normalized)))

    @staticmethod
    def _snapshot(
        history: PriceHistory,
    ) -> WatchlistMarketSnapshot | WatchlistMarketFailure:
        prices = _analysis_prices(history)
        if len(prices) < 2 or any(price <= 0 for price in prices):
            return WatchlistMarketFailure(
                symbol=history.symbol,
                code="provider_unavailable",
                message=f"Market data for {history.symbol} is incomplete.",
            )
        returns = [
            current / previous - 1
            for previous, current in zip(prices[:-1], prices[1:], strict=True)
        ]
        recent_returns = returns[-30:]
        volatility = (
            stdev(recent_returns) * sqrt(252) if len(recent_returns) >= 2 else None
        )
        previous_close = history.points[-2].close
        latest_price = history.points[-1].close
        daily_change = latest_price - previous_close
        return WatchlistMarketSnapshot(
            symbol=history.symbol,
            currency=history.currency,
            latest_price=latest_price,
            previous_close=previous_close,
            daily_change=daily_change,
            daily_change_percent=daily_change / previous_close,
            latest_observation_at=history.points[-1].timestamp,
            annualized_volatility_30d=volatility,
            maximum_drawdown_3m=_maximum_drawdown(prices),
            provider=history.provider,
            retrieved_at=history.retrieved_at,
            quality_warnings=history.quality_warnings,
        )
