"""Bounded repair and safe-delivery decisions for verified claims."""

from dataclasses import dataclass
from typing import Literal

from app.grounding.contracts import VerificationReport

DeliveryAction = Literal["deliver", "revise", "soften_or_remove", "limitation"]


@dataclass(frozen=True, slots=True)
class RepairPolicy:
    """Application-owned upper bound for targeted answer revisions."""

    max_attempts: int = 2

    def __post_init__(self) -> None:
        if self.max_attempts not in {1, 2}:
            raise ValueError("Repair attempts must be one or two.")


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    """Machine-readable next action after deterministic verification."""

    action: DeliveryAction
    blocking_claim_ids: tuple[str, ...] = ()


def decide_delivery(
    report: VerificationReport,
    *,
    attempts_used: int,
    policy: RepairPolicy | None = None,
) -> DeliveryDecision:
    """Revise only within budget, then withhold unsupported material."""
    policy = policy or RepairPolicy()
    blocking = tuple(
        dict.fromkeys(
            issue.claim_id for issue in report.issues if issue.severity == "blocking"
        )
    )
    if not blocking:
        return DeliveryDecision(action="deliver")
    if attempts_used < policy.max_attempts:
        return DeliveryDecision(action="revise", blocking_claim_ids=blocking)
    severe_codes = {
        issue.code for issue in report.issues if issue.severity == "blocking"
    }
    action: DeliveryAction = (
        "limitation"
        if severe_codes
        & {
            "fabricated_citation",
            "unauthorized_citation",
            "unavailable_evidence",
            "calculation_artifact_required",
        }
        else "soften_or_remove"
    )
    return DeliveryDecision(action=action, blocking_claim_ids=blocking)
