"""Provider-neutral fundamental research datasets."""

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite


def _json_value(value: object) -> object:
    """Convert nested provider-neutral values into artifact-safe JSON values."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class FundamentalDataset:
    """One bounded normalized dataset returned by a research tool."""

    symbol: str
    dataset: str
    currency: str | None
    records: tuple[dict[str, object], ...]
    provider: str
    retrieved_at: datetime
    source_urls: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "dataset": self.dataset,
            "currency": self.currency,
            "records": [_json_value(record) for record in self.records],
            "provider": self.provider,
            "retrieved_at": self.retrieved_at.isoformat(),
            "source_urls": list(self.source_urls),
        }
