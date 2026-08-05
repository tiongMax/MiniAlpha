"""Provider-neutral historical price data."""

from dataclasses import asdict, dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PricePoint:
    """One normalized OHLCV observation."""

    timestamp: datetime
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int | None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = asdict(self)
        data["timestamp"] = self.timestamp.isoformat()
        return data


@dataclass(frozen=True, slots=True)
class PriceHistory:
    """A bounded, chart-ready series for one public symbol."""

    symbol: str
    currency: str | None
    period: str
    interval: str
    points: tuple[PricePoint, ...]
    provider: str
    retrieved_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "currency": self.currency,
            "period": self.period,
            "interval": self.interval,
            "points": [point.to_dict() for point in self.points],
            "provider": self.provider,
            "retrieved_at": self.retrieved_at.isoformat(),
        }
