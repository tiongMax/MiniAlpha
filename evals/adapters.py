"""Adapters for evaluating MiniAlpha without modifying production behavior."""

from __future__ import annotations

from typing import Protocol

from evals.schema import EvaluationCase, ExecutionOutcome


class _ResearchResult(Protocol):
    answer: str
    tool_calls: tuple[object, ...]
    tool_results: tuple[object, ...]
    artifacts: tuple[dict[str, object], ...]


class _ResearchService(Protocol):
    async def research(self, message: str) -> _ResearchResult: ...


class LiveResearchServiceExecutor:
    """Optional adapter around the existing stateless ResearchAgentService."""

    def __init__(self, service: _ResearchService) -> None:
        self._service = service

    async def execute(
        self, case: EvaluationCase, *, trial_index: int
    ) -> ExecutionOutcome:
        """Run a live single-turn case and retain null usage fields if unavailable."""
        del trial_index
        if case.turns:
            raise ValueError(
                "The stateless live adapter cannot execute multi-turn cases; "
                "provide a checkpoint-aware executor."
            )
        result = await self._service.research(case.question)
        return ExecutionOutcome(
            final_answer=result.answer,
            tool_calls=tuple(
                {
                    "name": str(getattr(call, "name", "")),
                    "arguments": dict(getattr(call, "arguments", {})),
                    "status": getattr(call, "status", None),
                    "summary": getattr(call, "summary", None),
                }
                for call in result.tool_calls
            ),
            tool_results=tuple(
                {
                    "name": str(getattr(item, "name", "")),
                    "content": str(getattr(item, "content", "")),
                    "artifact": getattr(item, "artifact", None),
                }
                for item in result.tool_results
            ),
            artifacts=result.artifacts,
        )
