"""Credential-free executor backed by frozen provider/model outcomes."""

from __future__ import annotations

from evals.schema import EvaluationCase, ExecutionOutcome


class FrozenExecutor:
    """Return checked-in outcomes without network access or credentials."""

    def __init__(self, outcomes: dict[str, ExecutionOutcome]) -> None:
        self._outcomes = outcomes

    async def execute(
        self, case: EvaluationCase, *, trial_index: int
    ) -> ExecutionOutcome:
        """Return a frozen result; trial index is accepted for runner parity."""
        del trial_index
        try:
            return self._outcomes[case.fixture]
        except KeyError as error:
            raise KeyError(
                f"No frozen outcome exists for fixture {case.fixture!r}"
            ) from error
