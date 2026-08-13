"""Data-specific TTL policy accounts for source age and result safety."""

from datetime import UTC, datetime, timedelta

from app.cache.policy import evaluate_artifact_ttl

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)


def artifact(
    artifact_type: str,
    retrieved_at: datetime,
    *,
    timestamp_field: str = "retrieved_at",
) -> dict[str, object]:
    return {
        "artifact_type": artifact_type,
        "schema_version": 1,
        "status": "ok",
        "data": {timestamp_field: retrieved_at.isoformat()},
    }


def test_news_ttl_is_reduced_by_source_age() -> None:
    decision = evaluate_artifact_ttl(
        [artifact("company_news", NOW - timedelta(seconds=120))],
        now=NOW,
    )

    assert decision.cacheable
    assert decision.ttl_seconds == 180
    assert decision.expires_at == NOW + timedelta(seconds=180)
    assert decision.source_retrieved_at == NOW - timedelta(seconds=120)


def test_mixed_result_uses_shortest_remaining_ttl() -> None:
    decision = evaluate_artifact_ttl(
        [
            artifact("company_overview", NOW - timedelta(minutes=14)),
            artifact("ownership", NOW - timedelta(hours=1)),
        ],
        now=NOW,
    )

    assert decision.cacheable
    assert decision.ttl_seconds == 60
    assert decision.artifact_types == ("company_overview", "ownership")


def test_quantitative_ttl_uses_source_not_calculation_timestamp() -> None:
    item = artifact(
        "volatility",
        NOW - timedelta(minutes=10),
        timestamp_field="source_retrieved_at",
    )
    item["data"]["calculated_at"] = NOW.isoformat()  # type: ignore[index]

    decision = evaluate_artifact_ttl([item], now=NOW)

    assert decision.cacheable
    assert decision.ttl_seconds == 50 * 60


def test_expired_source_is_not_cached() -> None:
    decision = evaluate_artifact_ttl(
        [artifact("company_news", NOW - timedelta(minutes=5))],
        now=NOW,
    )

    assert not decision.cacheable
    assert decision.reason == "source_data_expired"


def test_error_partial_unknown_and_timestamp_free_results_are_not_cached() -> None:
    failed = artifact("company_overview", NOW)
    failed["status"] = "error"
    failed["data"] = None
    failed["error"] = "provider unavailable"

    assert evaluate_artifact_ttl([failed], now=NOW).reason == "non_success_artifact"
    assert (
        evaluate_artifact_ttl([artifact("unknown_dataset", NOW)], now=NOW).reason
        == "unrecognized_artifact_type"
    )
    missing_timestamp = artifact("company_overview", NOW)
    missing_timestamp["data"] = {}
    assert (
        evaluate_artifact_ttl([missing_timestamp], now=NOW).reason
        == "missing_source_timestamp"
    )
    assert (
        evaluate_artifact_ttl([], now=NOW, complete=False).reason == "incomplete_result"
    )


def test_source_free_conceptual_answer_gets_bounded_ttl() -> None:
    decision = evaluate_artifact_ttl([], now=NOW)

    assert decision.cacheable
    assert decision.ttl_seconds == 24 * 60 * 60
    assert decision.reason == "source_free_answer"
