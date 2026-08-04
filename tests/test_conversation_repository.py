"""Contract tests for conversation lifecycle persistence."""

import asyncio
from uuid import uuid4

import pytest

from app.persistence.artifacts import parse_artifact
from app.persistence.memory import InMemoryConversationRepository
from app.persistence.repository import (
    CheckpointConflictError,
    RequestKeyConflictError,
    RunInProgressError,
)


def run(coroutine):
    """Execute one repository coroutine."""
    return asyncio.run(coroutine)


def test_admits_and_completes_a_new_thread() -> None:
    """Verify a completed run publishes the thread checkpoint."""
    repository = InMemoryConversationRepository()

    admission = run(
        repository.admit_run(
            thread_id=None,
            message="Analyze Apple.",
            request_key=uuid4(),
        )
    )
    turn = run(
        repository.complete_run(
            admission.run.run_id,
            expected_checkpoint_id=None,
            checkpoint_id="checkpoint-1",
            answer="Apple is profitable.",
            tool_calls=[
                {
                    "name": "get_company_overview",
                    "arguments": {"symbol": "AAPL"},
                }
            ],
            artifacts=[
                {
                    "artifact_type": "company_overview",
                    "schema_version": 1,
                    "status": "ok",
                    "data": {"symbol": "AAPL"},
                }
            ],
        )
    )

    thread = run(repository.get_thread(admission.run.thread_id))
    assert admission.replayed is False
    assert turn.run.status == "completed"
    assert turn.artifacts[0].data == {"symbol": "AAPL"}
    assert thread is not None
    assert thread.latest_checkpoint_id == "checkpoint-1"
    assert thread.next_turn_index == 2


def test_replays_a_completed_request_key_without_new_turn() -> None:
    """Verify retransmission resolves to the original durable run."""
    repository = InMemoryConversationRepository()
    request_key = uuid4()
    first = run(
        repository.admit_run(
            thread_id=None,
            message="Analyze Apple.",
            request_key=request_key,
        )
    )
    run(
        repository.complete_run(
            first.run.run_id,
            expected_checkpoint_id=None,
            checkpoint_id="checkpoint-1",
            answer="Apple is profitable.",
            tool_calls=[],
            artifacts=[],
        )
    )

    replay = run(
        repository.admit_run(
            thread_id=first.run.thread_id,
            message="Analyze Apple.",
            request_key=request_key,
        )
    )
    thread = run(repository.get_thread(first.run.thread_id))

    assert replay.replayed is True
    assert replay.run.run_id == first.run.run_id
    assert replay.run.status == "completed"
    assert thread is not None
    assert thread.next_turn_index == 2


def test_rejects_request_key_reuse_for_different_input() -> None:
    """Verify idempotency keys cannot alias unrelated requests."""
    repository = InMemoryConversationRepository()
    request_key = uuid4()
    run(
        repository.admit_run(
            thread_id=None,
            message="Analyze Apple.",
            request_key=request_key,
        )
    )

    with pytest.raises(RequestKeyConflictError):
        run(
            repository.admit_run(
                thread_id=None,
                message="Analyze Microsoft.",
                request_key=request_key,
            )
        )


def test_allows_only_one_active_run_per_thread() -> None:
    """Verify concurrent turns cannot both own one thread."""
    repository = InMemoryConversationRepository()
    first = run(
        repository.admit_run(
            thread_id=None,
            message="Analyze Apple.",
            request_key=None,
        )
    )

    with pytest.raises(RunInProgressError):
        run(
            repository.admit_run(
                thread_id=first.run.thread_id,
                message="Compare it with Microsoft.",
                request_key=None,
            )
        )


def test_failed_run_preserves_checkpoint_and_allows_next_turn() -> None:
    """Verify failure is terminal without advancing graph memory."""
    repository = InMemoryConversationRepository()
    first = run(
        repository.admit_run(
            thread_id=None,
            message="Analyze Apple.",
            request_key=None,
        )
    )
    failed = run(
        repository.fail_run(
            first.run.run_id,
            error_code="research_failed",
            error_message="The research agent could not complete the request.",
        )
    )

    thread_after_failure = run(repository.get_thread(first.run.thread_id))
    second = run(
        repository.admit_run(
            thread_id=first.run.thread_id,
            message="Try again.",
            request_key=None,
        )
    )

    assert failed.status == "error"
    assert thread_after_failure is not None
    assert thread_after_failure.latest_checkpoint_id is None
    assert second.run.turn_index == 2
    assert second.from_checkpoint_id is None


def test_rejects_stale_checkpoint_publication() -> None:
    """Verify compare-and-swap protects the committed thread head."""
    repository = InMemoryConversationRepository()
    first = run(
        repository.admit_run(
            thread_id=None,
            message="Analyze Apple.",
            request_key=None,
        )
    )

    with pytest.raises(CheckpointConflictError):
        run(
            repository.complete_run(
                first.run.run_id,
                expected_checkpoint_id="stale-checkpoint",
                checkpoint_id="checkpoint-1",
                answer="Apple is profitable.",
                tool_calls=[],
                artifacts=[],
            )
        )


def test_rejects_malformed_artifact_envelopes() -> None:
    """Verify broad dictionaries are narrowed before persistence."""
    with pytest.raises(ValueError, match="schema version"):
        parse_artifact(
            {
                "artifact_type": "company_overview",
                "schema_version": "1",
                "status": "ok",
                "data": {"symbol": "AAPL"},
            }
        )

    with pytest.raises(ValueError, match="require an error"):
        parse_artifact(
            {
                "artifact_type": "company_overview",
                "schema_version": 1,
                "status": "error",
                "data": None,
            }
        )
