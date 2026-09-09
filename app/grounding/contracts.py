"""Provider-neutral claim and verification contracts."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

ClaimType = Literal[
    "retrieved_fact",
    "calculated_number",
    "comparison",
    "interpretation",
    "limitation",
]
NumericUnit = Literal["number", "decimal", "percent"]
NumericScale = Literal["one", "thousand", "million", "billion", "trillion"]
IssueSeverity = Literal["info", "warning", "blocking"]


@dataclass(frozen=True, slots=True)
class NumericValue:
    """A number with explicit display semantics.

    Decimal stores a fraction such as 0.125, while percent stores the displayed
    percentage 12.5. Scale is applied before comparison.
    """

    value: Decimal
    unit: NumericUnit = "number"
    scale: NumericScale = "one"
    currency: str | None = None


@dataclass(frozen=True, slots=True)
class Citation:
    """A reference to an exact value inside one structured artifact."""

    artifact_id: UUID
    path: str | None = None
    value_unit: NumericUnit = "number"
    value_scale: NumericScale = "one"


@dataclass(frozen=True, slots=True)
class Claim:
    """One important answer claim and its inspectable evidence references."""

    claim_id: str
    text: str
    claim_type: ClaimType
    citations: tuple[Citation, ...] = ()
    entity: str | None = None
    period: str | None = None
    numeric_value: NumericValue | None = None
    absolute_tolerance: Decimal = Decimal("0")
    relative_tolerance: Decimal = Decimal("0.000001")


@dataclass(frozen=True, slots=True)
class VerificationIssue:
    """One structured reason a claim should pass, soften, or be withheld."""

    code: str
    severity: IssueSeverity
    message: str
    claim_id: str
    artifact_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """Deterministic verification result for a collection of claims."""

    issues: tuple[VerificationIssue, ...]
    checked_claims: int
    checked_citations: int

    @property
    def passed(self) -> bool:
        return not any(issue.severity == "blocking" for issue in self.issues)

    def issues_for(self, claim_id: str) -> tuple[VerificationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.claim_id == claim_id)
