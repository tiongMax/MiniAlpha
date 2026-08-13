"""Data-specific retention policy for completed research results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

from app.cache.models import CachePolicyDecision

# Retention reflects source volatility, not how long a generated answer feels useful.
ARTIFACT_TTL_SECONDS: dict[str, int] = {
    "company_news": 5 * 60,
    "company_overview": 15 * 60,
    "fundamental_ratios": 15 * 60,
    "company_comparison": 15 * 60,
    "sec_filings": 30 * 60,
    "insider_activity": 30 * 60,
    "price_history": 60 * 60,
    "return_statistics": 60 * 60,
    "volatility": 60 * 60,
    "drawdown": 60 * 60,
    "correlation": 60 * 60,
    "technical_indicators": 60 * 60,
    "moving_average_backtest": 60 * 60,
    "analyst_estimates": 6 * 60 * 60,
    "financial_statements": 24 * 60 * 60,
    "ownership": 24 * 60 * 60,
}
ARTIFACT_TYPE_ALIASES: dict[str, str] = {
    "volatility_analysis": "volatility",
    "drawdown_analysis": "drawdown",
    "correlation_analysis": "correlation",
}
SOURCE_FREE_TTL_SECONDS = 24 * 60 * 60


def evaluate_artifact_ttl(
    artifacts: Sequence[Mapping[str, object]],
    *,
    now: datetime | None = None,
    complete: bool = True,
) -> CachePolicyDecision:
    """Return the shortest source-age-adjusted TTL across all artifacts.

    Failed, partial, unrecognized, timestamp-free, and already stale results
    are deliberately excluded. Answers with no artifacts are treated as
    source-free conceptual answers and receive a conservative one-day TTL.
    """
    current = _utc(now or datetime.now(UTC))
    if not complete:
        return _rejected("incomplete_result")
    if not artifacts:
        return CachePolicyDecision(
            cacheable=True,
            ttl_seconds=SOURCE_FREE_TTL_SECONDS,
            expires_at=current + timedelta(seconds=SOURCE_FREE_TTL_SECONDS),
            source_retrieved_at=None,
            artifact_types=(),
            reason="source_free_answer",
        )

    remaining_ttls: list[int] = []
    source_times: list[datetime] = []
    artifact_types: list[str] = []
    for artifact in artifacts:
        if artifact.get("status") != "ok":
            return _rejected("non_success_artifact")
        artifact_type = artifact.get("artifact_type")
        canonical_type = _canonical_artifact_type(artifact_type)
        if canonical_type is None:
            return _rejected("unrecognized_artifact_type")
        data = artifact.get("data")
        if not isinstance(data, Mapping):
            return _rejected("missing_artifact_data")
        retrieved_at = _source_timestamp(data)
        if retrieved_at is None:
            return _rejected("missing_source_timestamp")
        if retrieved_at > current + timedelta(minutes=5):
            return _rejected("future_source_timestamp")

        base_ttl = ARTIFACT_TTL_SECONDS[canonical_type]
        source_age = max(0, int((current - retrieved_at).total_seconds()))
        remaining = base_ttl - source_age
        if remaining <= 0:
            return _rejected("source_data_expired")
        remaining_ttls.append(remaining)
        source_times.append(retrieved_at)
        artifact_types.append(artifact_type)

    ttl = min(remaining_ttls)
    return CachePolicyDecision(
        cacheable=True,
        ttl_seconds=ttl,
        expires_at=current + timedelta(seconds=ttl),
        source_retrieved_at=min(source_times),
        artifact_types=tuple(artifact_types),
        reason="data_specific_ttl",
    )


def _source_timestamp(data: Mapping[str, object]) -> datetime | None:
    raw = data.get("source_retrieved_at", data.get("retrieved_at"))
    if isinstance(raw, datetime):
        return _utc(raw)
    if not isinstance(raw, str):
        return None
    try:
        return _utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        return None


def _canonical_artifact_type(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    if value in ARTIFACT_TTL_SECONDS:
        return value
    if value.startswith("financial_statements_"):
        return "financial_statements"
    if value in ARTIFACT_TYPE_ALIASES:
        return ARTIFACT_TYPE_ALIASES[value]
    return None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _rejected(reason: str) -> CachePolicyDecision:
    return CachePolicyDecision(
        cacheable=False,
        ttl_seconds=0,
        expires_at=None,
        source_retrieved_at=None,
        artifact_types=(),
        reason=reason,
    )
