"""Contracts for safe structured failures and their storage boundaries."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.agent.failures import (
    failure_for_financial_error,
    parse_structured_failure,
)
from app.api.schemas import ArtifactResponse
from app.domain.errors import (
    FinancialProviderTimeout,
    InvalidPriceQueryError,
    SymbolNotFoundError,
)
from app.persistence.artifacts import parse_artifact
from app.persistence.memory import InMemoryConversationRepository
from app.persistence.postgres_records import artifact_from_row, artifact_values


def _provider_failure() -> dict[str, object]:
    return failure_for_financial_error(
        FinancialProviderTimeout("Safe provider timeout."),
        operation="price_history",
        attempt=2,
        max_attempts=2,
        tool_call_id="call-price",
    ).to_dict()


def _error_artifact() -> dict[str, object]:
    return {
        "artifact_type": "price_history",
        "schema_version": 1,
        "status": "error",
        "error": "Price history is temporarily unavailable.",
        "failure": _provider_failure(),
    }


def test_classifies_domain_errors_without_exception_details() -> None:
    """Only stable recovery facts, never an exception representation, are emitted."""
    timeout = failure_for_financial_error(
        FinancialProviderTimeout("token=secret upstream traceback"),
        operation="company_overview",
    ).to_dict()
    invalid = failure_for_financial_error(
        InvalidPriceQueryError("Choose a supported interval."),
        operation="price_history",
    ).to_dict()
    missing = failure_for_financial_error(
        SymbolNotFoundError("No company data is available."),
        operation="company_overview",
    ).to_dict()

    assert timeout == {
        "schema_version": 1,
        "code": "provider_timeout",
        "category": "provider",
        "source": "yahoo_finance",
        "operation": "company_overview",
        "retryable": True,
        "attempt": 1,
        "max_attempts": 1,
        "recovery": "retry",
    }
    assert "secret" not in str(timeout)
    assert invalid["code"] == "invalid_price_query"
    assert invalid["recovery"] == "model_correction"
    assert missing["code"] == "symbol_not_found"
    assert missing["retryable"] is False


def test_rejects_incoherent_failure_metadata() -> None:
    """Invalid attempts, retry policies, and unknown fields cannot be persisted."""
    invalid_attempt = _provider_failure()
    invalid_attempt["attempt"] = 3
    with pytest.raises(ValueError, match="exceeds"):
        parse_structured_failure(invalid_attempt)

    invalid_retry = _provider_failure()
    invalid_retry["retryable"] = False
    with pytest.raises(ValueError, match="requires a retryable"):
        parse_structured_failure(invalid_retry)

    unknown = _provider_failure()
    unknown["exception"] = "raw details"
    with pytest.raises(ValueError, match="unsupported fields"):
        parse_structured_failure(unknown)


def test_artifact_parser_accepts_legacy_and_structured_errors() -> None:
    """Legacy errors remain readable while new failures are normalized strictly."""
    legacy = parse_artifact(
        {
            "artifact_type": "company_overview",
            "schema_version": 1,
            "status": "error",
            "error": "No data is available.",
        }
    )
    structured = parse_artifact(_error_artifact())

    assert legacy.error == "No data is available."
    assert legacy.failure is None
    assert structured.failure == _provider_failure()

    with pytest.raises(ValueError, match="no error or failure"):
        parse_artifact(
            {
                "artifact_type": "company_overview",
                "schema_version": 1,
                "status": "ok",
                "data": {"symbol": "AAPL"},
                "failure": _provider_failure(),
            }
        )


def test_memory_repository_and_api_schema_preserve_failure() -> None:
    """A structured failure survives repository and public-schema round trips."""

    async def store():
        repository = InMemoryConversationRepository()
        admission = await repository.admit_run(
            thread_id=None,
            message="Get Apple prices.",
            request_key=uuid4(),
        )
        turn = await repository.complete_run(
            admission.run.run_id,
            expected_checkpoint_id=None,
            checkpoint_id="checkpoint-1",
            answer="The provider is temporarily unavailable.",
            tool_calls=[],
            artifacts=[_error_artifact()],
        )
        return turn.artifacts[0]

    stored = asyncio.run(store())
    response = ArtifactResponse(
        artifact_type=stored.artifact_type,
        schema_version=stored.schema_version,
        status=stored.status,
        data=stored.data,
        error=stored.error,
        failure=stored.failure,
    ).model_dump(exclude_none=True)

    assert stored.failure == _provider_failure()
    assert response["error"] == "Price history is temporarily unavailable."
    assert response["failure"] == _provider_failure()


def test_postgres_record_conversions_include_failure_json() -> None:
    """SQL parameters and hydrated records retain the structured object."""
    values = artifact_values(4, _error_artifact())
    assert values[:6] == (
        4,
        "price_history",
        1,
        "error",
        None,
        "Price history is temporarily unavailable.",
    )
    assert values[6] is not None

    now = datetime(2026, 8, 13, tzinfo=UTC)
    row = {
        "artifact_id": uuid4(),
        "conversation_response_id": uuid4(),
        "ordinal": 4,
        "artifact_type": "price_history",
        "schema_version": 1,
        "status": "error",
        "data": None,
        "error": "Price history is temporarily unavailable.",
        "failure": _provider_failure(),
        "created_at": now,
    }
    stored = artifact_from_row(row)
    assert stored.failure == _provider_failure()
    assert stored.created_at == now
