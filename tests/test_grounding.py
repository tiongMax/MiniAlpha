"""Credential-free tests for provenance and deterministic claim verification."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.grounding.contracts import Citation, Claim, NumericValue
from app.grounding.policy import RepairPolicy, decide_delivery
from app.grounding.provenance import attach_provenance
from app.grounding.verifier import DeterministicClaimVerifier
from app.persistence.artifacts import parse_artifact


def _artifact(
    *,
    artifact_id: UUID | None = None,
    entity: str = "AAPL",
    period: str = "1y",
    currency: str = "USD",
    calculation: str | None = None,
    freshness: str = "current",
) -> dict[str, object]:
    identity = artifact_id or uuid4()
    return {
        "artifact_id": str(identity),
        "artifact_type": "return_statistics" if calculation else "company_overview",
        "schema_version": 1,
        "status": "ok",
        "data": {
            "symbol": entity,
            "market_cap": 3_000_000_000,
            "profit_margin": 0.125,
            "summary": {"total_return": 0.2},
        },
        "provenance": {
            "entities": [entity],
            "reporting_period": period,
            "currency": currency,
            "calculation_name": calculation,
            "freshness_status": freshness,
        },
    }


def _codes(report: object) -> set[str]:
    return {issue.code for issue in report.issues}  # type: ignore[attr-defined]


def test_provenance_preserves_source_metadata_and_explicit_unknowns() -> None:
    artifact = attach_provenance(
        {
            "artifact_type": "company_news",
            "schema_version": 1,
            "status": "ok",
            "data": {
                "symbol": "AAPL",
                "provider": "Yahoo Finance",
                "retrieved_at": "2026-08-10T00:00:00+00:00",
                "source_urls": ["https://example.test/story"],
            },
        }
    )

    UUID(str(artifact["artifact_id"]))
    provenance = artifact["provenance"]
    assert isinstance(provenance, dict)
    assert provenance["provider"] == "Yahoo Finance"
    assert provenance["entities"] == ["AAPL"]
    assert provenance["source_urls"] == ["https://example.test/story"]
    assert provenance["reporting_period"] is None
    assert provenance["calculation_version"] is None


def test_legacy_artifact_remains_valid_without_identity_or_provenance() -> None:
    parsed = parse_artifact(
        {
            "artifact_type": "company_overview",
            "schema_version": 1,
            "status": "ok",
            "data": {"symbol": "AAPL"},
        }
    )
    assert parsed.artifact_id is None
    assert parsed.provenance is None


def test_valid_numeric_citation_normalizes_percent_and_decimal() -> None:
    artifact = _artifact()
    identity = UUID(str(artifact["artifact_id"]))
    claim = Claim(
        claim_id="margin",
        text="Apple's profit margin was 12.5%.",
        claim_type="retrieved_fact",
        citations=(Citation(identity, "profit_margin", value_unit="decimal"),),
        entity="AAPL",
        period="1y",
        numeric_value=NumericValue(Decimal("12.5"), unit="percent"),
    )
    report = DeterministicClaimVerifier([artifact]).verify([claim])
    assert report.passed


def test_numeric_citation_normalizes_billion_scale() -> None:
    artifact = _artifact()
    identity = UUID(str(artifact["artifact_id"]))
    claim = Claim(
        claim_id="market-cap",
        text="Apple's market cap was USD 3 billion.",
        claim_type="retrieved_fact",
        citations=(Citation(identity, "market_cap"),),
        entity="AAPL",
        period="1y",
        numeric_value=NumericValue(Decimal("3"), scale="billion", currency="USD"),
    )
    assert DeterministicClaimVerifier([artifact]).verify([claim]).passed


def test_fabricated_and_unauthorized_citations_are_blocking() -> None:
    artifact = _artifact()
    identity = UUID(str(artifact["artifact_id"]))
    missing = uuid4()
    claims = [
        Claim("fabricated", "Unsupported", "retrieved_fact", (Citation(missing),)),
        Claim("outside", "Wrong scope", "retrieved_fact", (Citation(identity),)),
    ]
    report = DeterministicClaimVerifier(
        [artifact], authorized_artifact_ids=set()
    ).verify(claims)
    assert _codes(report) == {"fabricated_citation", "unauthorized_citation"}
    assert not report.passed


@pytest.mark.parametrize(
    ("entity", "period", "currency", "expected"),
    [
        ("MSFT", "1y", "USD", "entity_mismatch"),
        ("AAPL", "5y", "USD", "period_mismatch"),
        ("AAPL", "1y", "EUR", "currency_mismatch"),
    ],
)
def test_entity_period_and_currency_mismatches_block(
    entity: str, period: str, currency: str, expected: str
) -> None:
    artifact = _artifact()
    identity = UUID(str(artifact["artifact_id"]))
    claim = Claim(
        claim_id="alignment",
        text="Aligned value",
        claim_type="retrieved_fact",
        citations=(Citation(identity, "market_cap"),),
        entity=entity,
        period=period,
        numeric_value=NumericValue(Decimal("3000000000"), currency=currency),
    )
    report = DeterministicClaimVerifier([artifact]).verify([claim])
    assert expected in _codes(report)
    assert not report.passed


def test_stale_evidence_is_visible_but_not_silently_rejected() -> None:
    artifact = _artifact(freshness="stale")
    identity = UUID(str(artifact["artifact_id"]))
    claim = Claim(
        "stale",
        "Potentially stale fact",
        "retrieved_fact",
        (Citation(identity),),
        entity="AAPL",
        period="1y",
    )
    report = DeterministicClaimVerifier([artifact]).verify([claim])
    assert _codes(report) == {"stale_evidence"}
    assert report.passed


def test_calculated_claim_requires_deterministic_calculation_artifact() -> None:
    artifact = _artifact(calculation=None)
    identity = UUID(str(artifact["artifact_id"]))
    claim = Claim(
        "return",
        "The total return was 20%.",
        "calculated_number",
        (Citation(identity, "summary.total_return", value_unit="decimal"),),
        entity="AAPL",
        period="1y",
        numeric_value=NumericValue(Decimal("20"), unit="percent"),
    )
    report = DeterministicClaimVerifier([artifact]).verify([claim])
    assert "calculation_artifact_required" in _codes(report)


def test_bounded_repair_then_withholds_unsupported_claim() -> None:
    missing_claim = Claim("bad", "No evidence", "retrieved_fact")
    failed = DeterministicClaimVerifier([]).verify([missing_claim])
    assert decide_delivery(failed, attempts_used=0).action == "revise"
    exhausted = decide_delivery(
        failed, attempts_used=2, policy=RepairPolicy(max_attempts=2)
    )
    assert exhausted.action == "soften_or_remove"
    successful = DeterministicClaimVerifier([]).verify(
        [Claim("limit", "Data was unavailable.", "limitation")]
    )
    assert decide_delivery(successful, attempts_used=1).action == "deliver"
