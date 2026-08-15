"""Stable, model-readable failure metadata for recoverable agent work."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from app.domain.errors import (
    FinancialDataError,
    FinancialProviderError,
    FinancialProviderTimeout,
    InvalidFundamentalQueryError,
    InvalidPriceQueryError,
    InvalidQuantitativeQueryError,
    InvalidSymbolError,
    SymbolNotFoundError,
)

FailureCategory = Literal["tool_input", "provider", "tool_runtime"]
FailureRecovery = Literal[
    "model_correction",
    "retry",
    "exhausted",
    "degraded",
    "continue_without_tool",
]


@dataclass(frozen=True, slots=True)
class StructuredFailure:
    """Versioned, safe failure facts carried beside an artifact error string."""

    code: str
    category: FailureCategory
    source: str
    operation: str
    retryable: bool
    attempt: int
    max_attempts: int
    recovery: FailureRecovery
    tool_call_id: str | None = None
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe public representation."""
        value: dict[str, object] = {
            "schema_version": self.schema_version,
            "code": self.code,
            "category": self.category,
            "source": self.source,
            "operation": self.operation,
            "retryable": self.retryable,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "recovery": self.recovery,
        }
        if self.tool_call_id is not None:
            value["tool_call_id"] = self.tool_call_id
        return value


def failure_for_financial_error(
    error: FinancialDataError,
    *,
    operation: str,
    attempt: int = 1,
    max_attempts: int = 1,
    tool_call_id: str | None = None,
) -> StructuredFailure:
    """Classify a safe domain error without exposing exception internals."""
    if isinstance(error, InvalidSymbolError):
        code = "invalid_symbol"
        category: FailureCategory = "tool_input"
        source = "financial_tool"
        retryable = False
        recovery: FailureRecovery = "model_correction"
    elif isinstance(error, InvalidPriceQueryError):
        code = "invalid_price_query"
        category = "tool_input"
        source = "financial_tool"
        retryable = False
        recovery = "model_correction"
    elif isinstance(error, InvalidFundamentalQueryError):
        code = "invalid_fundamental_query"
        category = "tool_input"
        source = "financial_tool"
        retryable = False
        recovery = "model_correction"
    elif isinstance(error, InvalidQuantitativeQueryError):
        code = "invalid_quantitative_query"
        category = "tool_input"
        source = "financial_tool"
        retryable = False
        recovery = "model_correction"
    elif isinstance(error, SymbolNotFoundError):
        code = "symbol_not_found"
        category = "provider"
        source = "yahoo_finance"
        retryable = False
        recovery = "model_correction"
    elif isinstance(error, FinancialProviderTimeout):
        code = "provider_timeout"
        category = "provider"
        source = "yahoo_finance"
        retryable = True
        recovery = "retry"
    elif isinstance(error, FinancialProviderError):
        code = "provider_unavailable"
        category = "provider"
        source = "yahoo_finance"
        retryable = True
        recovery = "retry"
    else:
        code = "financial_data_error"
        category = "tool_runtime"
        source = "financial_tool"
        retryable = False
        recovery = "continue_without_tool"

    return StructuredFailure(
        code=code,
        category=category,
        source=source,
        operation=operation,
        retryable=retryable,
        attempt=attempt,
        max_attempts=max_attempts,
        recovery=recovery,
        tool_call_id=tool_call_id,
    )


def parse_structured_failure(value: Mapping[str, object]) -> StructuredFailure:
    """Validate failure metadata arriving from graph or persistence boundaries."""
    allowed = {
        "schema_version",
        "code",
        "category",
        "source",
        "operation",
        "retryable",
        "attempt",
        "max_attempts",
        "recovery",
        "tool_call_id",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("Structured failure contains unsupported fields.")

    schema_version = value.get("schema_version")
    if schema_version != 1 or isinstance(schema_version, bool):
        raise ValueError("Structured failure schema version must be 1.")

    code = _required_identifier(value, "code")
    source = _required_identifier(value, "source")
    operation = _required_identifier(value, "operation")

    raw_category = value.get("category")
    if raw_category not in {"tool_input", "provider", "tool_runtime"}:
        raise ValueError("Structured failure category is unsupported.")
    category = cast(FailureCategory, raw_category)

    raw_retryable = value.get("retryable")
    if not isinstance(raw_retryable, bool):
        raise ValueError("Structured failure retryable must be a boolean.")

    attempt = _required_positive_integer(value, "attempt")
    max_attempts = _required_positive_integer(value, "max_attempts")
    if attempt > max_attempts:
        raise ValueError("Structured failure attempt exceeds max_attempts.")

    raw_recovery = value.get("recovery")
    if raw_recovery not in {
        "model_correction",
        "retry",
        "exhausted",
        "degraded",
        "continue_without_tool",
    }:
        raise ValueError("Structured failure recovery is unsupported.")
    recovery = cast(FailureRecovery, raw_recovery)
    if recovery == "retry" and not raw_retryable:
        raise ValueError("Retry recovery requires a retryable failure.")

    tool_call_id = value.get("tool_call_id")
    if tool_call_id is not None and (
        not isinstance(tool_call_id, str) or not tool_call_id.strip()
    ):
        raise ValueError("Structured failure tool_call_id must be non-empty.")

    return StructuredFailure(
        code=code,
        category=category,
        source=source,
        operation=operation,
        retryable=raw_retryable,
        attempt=attempt,
        max_attempts=max_attempts,
        recovery=recovery,
        tool_call_id=cast(str | None, tool_call_id),
        schema_version=1,
    )


def _required_identifier(value: Mapping[str, object], field: str) -> str:
    raw = value.get(field)
    if (
        not isinstance(raw, str)
        or not raw.strip()
        or len(raw) > 100
        or any(character.isspace() for character in raw)
    ):
        raise ValueError(f"Structured failure {field} must be an identifier.")
    return raw


def _required_positive_integer(value: Mapping[str, object], field: str) -> int:
    raw = value.get(field)
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 1:
        raise ValueError(f"Structured failure {field} must be a positive integer.")
    return raw
