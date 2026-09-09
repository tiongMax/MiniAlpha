"""Artifact identity and explicit provenance extraction."""

from collections.abc import Mapping
from typing import cast
from uuid import UUID, uuid4

_CALCULATION_ARTIFACTS = {
    "return_statistics",
    "volatility_analysis",
    "drawdown_analysis",
    "correlation_analysis",
    "technical_indicators",
    "moving_average_backtest",
}


def _strings(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _entities(data: Mapping[str, object]) -> list[str]:
    symbol = data.get("symbol")
    if isinstance(symbol, str) and symbol:
        return [symbol]
    symbols = _strings(data.get("symbols"))
    if symbols:
        return symbols
    records = data.get("records")
    if not isinstance(records, list):
        return []
    return [
        symbol
        for record in records
        if isinstance(record, dict)
        for symbol in [record.get("symbol")]
        if isinstance(symbol, str) and symbol
    ]


def provenance_from_artifact(artifact: Mapping[str, object]) -> dict[str, object]:
    """Derive only metadata already supplied by the artifact producer.

    Unknown fields remain None or empty. In particular, a calculation version,
    source URL, reporting period, or freshness result is never guessed.
    """
    raw_data = artifact.get("data")
    data = cast(Mapping[str, object], raw_data) if isinstance(raw_data, dict) else {}
    artifact_type = artifact.get("artifact_type")
    source_urls = _strings(data.get("source_urls"))
    retrieved_at = data.get("retrieved_at")
    if not isinstance(retrieved_at, str):
        retrieved_at = data.get("source_retrieved_at")
    period = data.get("period") if isinstance(data.get("period"), str) else None
    currency = data.get("currency") if isinstance(data.get("currency"), str) else None
    provider = data.get("provider") if isinstance(data.get("provider"), str) else None
    calculation_name = (
        data.get("analysis")
        if artifact_type in _CALCULATION_ARTIFACTS
        and isinstance(data.get("analysis"), str)
        else None
    )
    return {
        "provider": provider,
        "source_urls": source_urls,
        "retrieved_at": retrieved_at if isinstance(retrieved_at, str) else None,
        "reporting_period": period,
        "currency": currency,
        "unit": None,
        "entities": _entities(data),
        "parent_artifact_ids": [],
        "calculation_name": calculation_name,
        "calculation_version": None,
        "verification_status": "unverified",
        "freshness_status": "unknown",
    }


def attach_provenance(
    artifact: Mapping[str, object],
    *,
    artifact_id: UUID | None = None,
) -> dict[str, object]:
    """Return a backward-compatible artifact with stable app-owned identity."""
    grounded = dict(artifact)
    raw_id = grounded.get("artifact_id")
    if isinstance(raw_id, UUID):
        identity = raw_id
    elif isinstance(raw_id, str):
        identity = UUID(raw_id)
    else:
        identity = artifact_id or uuid4()
    grounded["artifact_id"] = str(identity)
    raw_provenance = grounded.get("provenance")
    grounded["provenance"] = (
        dict(raw_provenance)
        if isinstance(raw_provenance, dict)
        else provenance_from_artifact(grounded)
    )
    return grounded
