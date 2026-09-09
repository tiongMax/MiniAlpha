"""Compact market snapshots for watchlist refreshes."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True, slots=True)
class WatchlistMarketSnapshot:
    """Latest price and bounded risk measures for one symbol."""

    symbol: str
    currency: str | None
    latest_price: float
    previous_close: float
    daily_change: float
    daily_change_percent: float
    latest_observation_at: datetime
    annualized_volatility_30d: float | None
    maximum_drawdown_3m: float
    provider: str
    retrieved_at: datetime
    quality_warnings: tuple[str, ...] = ()
    status: Literal["ok"] = "ok"


@dataclass(frozen=True, slots=True)
class WatchlistMarketFailure:
    """Safe per-symbol failure that preserves other batch results."""

    symbol: str
    code: Literal["symbol_not_found", "provider_unavailable"]
    message: str
    status: Literal["error"] = "error"
