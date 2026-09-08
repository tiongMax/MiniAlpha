"""Deterministic verification for artifact-backed answer claims."""

from collections.abc import Awaitable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast
from uuid import UUID

from app.grounding.contracts import (
    Citation,
    Claim,
    IssueSeverity,
    NumericScale,
    NumericUnit,
    NumericValue,
    VerificationIssue,
    VerificationReport,
)

_SCALES: dict[NumericScale, Decimal] = {
    "one": Decimal("1"),
    "thousand": Decimal("1000"),
    "million": Decimal("1000000"),
    "billion": Decimal("1000000000"),
    "trillion": Decimal("1000000000000"),
}


class SemanticVerifier(Protocol):
    """Optional isolated verifier for interpretive claims."""

    def verify(
        self,
        claim: Claim,
        evidence: tuple["EvidenceArtifact", ...],
    ) -> Awaitable[tuple[VerificationIssue, ...]]: ...


@dataclass(frozen=True, slots=True)
class EvidenceArtifact:
    """Narrow artifact view used by verification and context workstreams."""

    artifact_id: UUID
    artifact_type: str
    status: str
    data: Mapping[str, object] | None
    provenance: Mapping[str, object]

    @classmethod
    def from_mapping(cls, artifact: Mapping[str, object]) -> "EvidenceArtifact":
        raw_id = artifact.get("artifact_id")
        artifact_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))
        raw_data = artifact.get("data")
        raw_provenance = artifact.get("provenance")
        return cls(
            artifact_id=artifact_id,
            artifact_type=str(artifact.get("artifact_type", "")),
            status=str(artifact.get("status", "")),
            data=raw_data if isinstance(raw_data, Mapping) else None,
            provenance=(raw_provenance if isinstance(raw_provenance, Mapping) else {}),
        )


class DeterministicClaimVerifier:
    """Verify claim citations against an authorized artifact scope."""

    def __init__(
        self,
        artifacts: Sequence[Mapping[str, object] | EvidenceArtifact],
        *,
        authorized_artifact_ids: set[UUID] | None = None,
    ) -> None:
        parsed = [
            artifact
            if isinstance(artifact, EvidenceArtifact)
            else EvidenceArtifact.from_mapping(artifact)
            for artifact in artifacts
        ]
        self._artifacts = {artifact.artifact_id: artifact for artifact in parsed}
        self._authorized = (
            authorized_artifact_ids
            if authorized_artifact_ids is not None
            else set(self._artifacts)
        )

    def verify(self, claims: Sequence[Claim]) -> VerificationReport:
        issues: list[VerificationIssue] = []
        checked_citations = 0
        for claim in claims:
            if claim.claim_type != "limitation" and not claim.citations:
                issues.append(
                    self._issue(
                        claim,
                        "missing_citation",
                        "blocking",
                        "The claim requires at least one artifact citation.",
                    )
                )
                continue
            cited: list[tuple[Citation, EvidenceArtifact]] = []
            for citation in claim.citations:
                checked_citations += 1
                artifact = self._artifacts.get(citation.artifact_id)
                if artifact is None:
                    issues.append(
                        self._issue(
                            claim,
                            "fabricated_citation",
                            "blocking",
                            "The cited artifact does not exist.",
                            citation.artifact_id,
                        )
                    )
                    continue
                if citation.artifact_id not in self._authorized:
                    issues.append(
                        self._issue(
                            claim,
                            "unauthorized_citation",
                            "blocking",
                            (
                                "The cited artifact is outside the current "
                                "run/thread scope."
                            ),
                            citation.artifact_id,
                        )
                    )
                    continue
                if artifact.status != "ok" or artifact.data is None:
                    issues.append(
                        self._issue(
                            claim,
                            "unavailable_evidence",
                            "blocking",
                            "The cited artifact contains no successful evidence.",
                            citation.artifact_id,
                        )
                    )
                    continue
                cited.append((citation, artifact))
                issues.extend(self._alignment_issues(claim, artifact))

            if claim.claim_type == "calculated_number" and cited:
                if not any(
                    isinstance(artifact.provenance.get("calculation_name"), str)
                    and artifact.provenance.get("calculation_name")
                    for _, artifact in cited
                ):
                    issues.append(
                        self._issue(
                            claim,
                            "calculation_artifact_required",
                            "blocking",
                            (
                                "Calculated claims require a deterministic "
                                "calculation artifact."
                            ),
                        )
                    )
            if claim.numeric_value is not None and cited:
                issues.extend(self._verify_number(claim, cited))

        return VerificationReport(
            issues=tuple(issues),
            checked_claims=len(claims),
            checked_citations=checked_citations,
        )

    def _alignment_issues(
        self, claim: Claim, artifact: EvidenceArtifact
    ) -> list[VerificationIssue]:
        issues: list[VerificationIssue] = []
        entities = artifact.provenance.get("entities")
        normalized_entities = (
            {entity.upper() for entity in entities if isinstance(entity, str)}
            if isinstance(entities, (list, tuple))
            else set()
        )
        if claim.entity and claim.entity.upper() not in normalized_entities:
            issues.append(
                self._issue(
                    claim,
                    "entity_mismatch",
                    "blocking",
                    "The cited artifact does not match the claim entity.",
                    artifact.artifact_id,
                )
            )
        evidence_period = artifact.provenance.get("reporting_period")
        if claim.period and claim.period != evidence_period:
            issues.append(
                self._issue(
                    claim,
                    "period_mismatch",
                    "blocking",
                    "The cited artifact does not match the claim period.",
                    artifact.artifact_id,
                )
            )
        if artifact.provenance.get("freshness_status") == "stale":
            issues.append(
                self._issue(
                    claim,
                    "stale_evidence",
                    "warning",
                    "The cited artifact is marked stale.",
                    artifact.artifact_id,
                )
            )
        return issues

    def _verify_number(
        self,
        claim: Claim,
        cited: list[tuple[Citation, EvidenceArtifact]],
    ) -> list[VerificationIssue]:
        assert claim.numeric_value is not None
        issues: list[VerificationIssue] = []
        matched = False
        for citation, artifact in cited:
            if citation.path is None:
                continue
            raw_value = _read_path(artifact.data, citation.path)
            if raw_value is None:
                issues.append(
                    self._issue(
                        claim,
                        "missing_evidence_value",
                        "blocking",
                        f"No value exists at citation path {citation.path!r}.",
                        artifact.artifact_id,
                    )
                )
                continue
            try:
                evidence_value = NumericValue(
                    value=Decimal(str(raw_value)),
                    unit=citation.value_unit,
                    scale=citation.value_scale,
                    currency=_currency(artifact.provenance),
                )
            except (InvalidOperation, ValueError):
                issues.append(
                    self._issue(
                        claim,
                        "non_numeric_evidence",
                        "blocking",
                        "The cited evidence value is not numeric.",
                        artifact.artifact_id,
                    )
                )
                continue
            if not _units_compatible(claim.numeric_value.unit, evidence_value.unit):
                issues.append(
                    self._issue(
                        claim,
                        "unit_mismatch",
                        "blocking",
                        "The claim and cited value use incompatible units.",
                        artifact.artifact_id,
                    )
                )
                continue
            if (
                claim.numeric_value.currency
                and evidence_value.currency
                and claim.numeric_value.currency.upper()
                != evidence_value.currency.upper()
            ):
                issues.append(
                    self._issue(
                        claim,
                        "currency_mismatch",
                        "blocking",
                        "The claim and cited artifact use different currencies.",
                        artifact.artifact_id,
                    )
                )
                continue
            expected = _canonical(claim.numeric_value)
            actual = _canonical(evidence_value)
            difference = abs(expected - actual)
            allowed = max(
                claim.absolute_tolerance,
                abs(actual) * claim.relative_tolerance,
            )
            if difference <= allowed:
                matched = True
                break
        if not matched and not any(
            issue.code
            in {
                "missing_evidence_value",
                "non_numeric_evidence",
                "unit_mismatch",
                "currency_mismatch",
            }
            for issue in issues
        ):
            issues.append(
                self._issue(
                    claim,
                    "unsupported_number",
                    "blocking",
                    "The structured claim value does not match its cited evidence.",
                )
            )
        return issues

    @staticmethod
    def _issue(
        claim: Claim,
        code: str,
        severity: str,
        message: str,
        artifact_id: UUID | None = None,
    ) -> VerificationIssue:
        return VerificationIssue(
            code=code,
            severity=cast(IssueSeverity, severity),
            message=message,
            claim_id=claim.claim_id,
            artifact_id=artifact_id,
        )


def _read_path(data: Mapping[str, object] | None, path: str) -> object | None:
    current: object = data
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        elif isinstance(current, (list, tuple)) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _canonical(value: NumericValue) -> Decimal:
    scaled = value.value * _SCALES[value.scale]
    return scaled / Decimal("100") if value.unit == "percent" else scaled


def _units_compatible(left: NumericUnit, right: NumericUnit) -> bool:
    return left == right or {left, right} == {"decimal", "percent"}


def _currency(provenance: Mapping[str, object]) -> str | None:
    currency = provenance.get("currency")
    return currency if isinstance(currency, str) else None
