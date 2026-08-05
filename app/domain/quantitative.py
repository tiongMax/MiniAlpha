"""Deterministic quantitative-analysis result contracts."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, float):
        return value if isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class QuantitativeDataset:
    """One bounded calculation derived from normalized market prices."""

    analysis: str
    symbols: tuple[str, ...]
    period: str
    interval: str
    parameters: dict[str, object]
    summary: dict[str, object]
    series: tuple[dict[str, object], ...]
    provider: str
    source_retrieved_at: datetime
    calculated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "analysis": self.analysis,
            "symbols": list(self.symbols),
            "period": self.period,
            "interval": self.interval,
            "parameters": _json_value(self.parameters),
            "summary": _json_value(self.summary),
            "series": [_json_value(record) for record in self.series],
            "provider": self.provider,
            "source_retrieved_at": self.source_retrieved_at.isoformat(),
            "calculated_at": self.calculated_at.isoformat(),
        }
