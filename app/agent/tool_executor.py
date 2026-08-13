"""Per-call tool isolation, retry, timeout, and structured degradation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import cast

from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from app.agent.failures import FailureCategory, FailureRecovery, StructuredFailure
from app.agent.retry import RetryPolicy
from app.domain.errors import FinancialProviderError, FinancialProviderTimeout
from app.observability import observe_span


class IsolatedToolExecutor:
    """Execute sibling calls independently and preserve one result per call."""

    def __init__(
        self,
        tools: Sequence[BaseTool],
        *,
        timeout_seconds: float,
        retry_policy: RetryPolicy | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Tool timeout must be positive.")
        self._tools = {tool.name: tool for tool in tools}
        self._timeout = timeout_seconds
        self._retry = retry_policy or RetryPolicy()
        self._sleeper = sleeper

    async def ainvoke(self, state: dict[str, object]) -> dict[str, list[ToolMessage]]:
        """Run the last model message's calls concurrently without batch failure."""
        messages = state.get("messages")
        if not isinstance(messages, list) or not messages:
            return {"messages": []}
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return {"messages": []}
        results = await asyncio.gather(
            *(self._execute(call) for call in last.tool_calls),
        )
        return {"messages": list(results)}

    async def _execute(self, raw_call: ToolCall) -> ToolMessage:
        """Attribute one isolated tool trajectory without recording arguments."""
        raw_name = str(raw_call.get("name", ""))
        name = raw_name if raw_name in self._tools else "unknown"
        with observe_span(
            "tool.execute",
            run_type="tool",
            metadata={
                "tool_name": name,
                "attempt_budget": self._retry.max_attempts,
            },
        ) as span:
            result, attempt = await self._execute_call(raw_call)
            artifact = result.artifact if isinstance(result.artifact, dict) else {}
            failure = artifact.get("failure")
            failure = failure if isinstance(failure, dict) else {}
            if artifact.get("status") == "error":
                span.mark_error_type(str(failure.get("code", "tool_error")))
            span.set_attributes(
                {
                    "outcome": "error" if artifact.get("status") == "error" else "ok",
                    "attempt": attempt,
                    "attempt_count": attempt,
                    "failure_code": failure.get("code"),
                    "failure_category": failure.get("category"),
                    "recovery": failure.get("recovery"),
                }
            )
            return result

    async def _execute_call(self, raw_call: ToolCall) -> tuple[ToolMessage, int]:
        """Execute one tool call under the configured isolation policy."""
        call = cast(ToolCall, raw_call)
        name = str(call.get("name", ""))
        call_id = str(call.get("id") or "unknown-call")
        tool = self._tools.get(name)
        if tool is None:
            return (
                _failure_message(
                    name=name or "unknown",
                    call_id=call_id,
                    code="unknown_tool",
                    category="tool_input",
                    operation=name or "unknown",
                    retryable=False,
                    attempt=1,
                    max_attempts=1,
                    recovery="model_correction",
                    public_message=(
                        "The requested tool is not available for this request."
                    ),
                ),
                1,
            )

        for attempt in range(1, self._retry.max_attempts + 1):
            try:
                async with asyncio.timeout(self._timeout):
                    result = await tool.ainvoke(call)
                if not isinstance(result, ToolMessage):
                    raise TypeError("Tool invocation did not return a ToolMessage.")
                if _artifact_is_retryable(result):
                    if attempt < self._retry.max_attempts:
                        await self._sleeper(self._retry.delay(attempt))
                        continue
                    return (
                        _with_attempt_metadata(
                            result,
                            attempt=attempt,
                            max_attempts=self._retry.max_attempts,
                            recovery="exhausted",
                            tool_call_id=call_id,
                        ),
                        attempt,
                    )
                if _is_error_artifact(result):
                    return (
                        _with_attempt_metadata(
                            result,
                            attempt=attempt,
                            max_attempts=self._retry.max_attempts,
                            tool_call_id=call_id,
                        ),
                        attempt,
                    )
                return result, attempt
            except TimeoutError:
                if attempt < self._retry.max_attempts:
                    await self._sleeper(self._retry.delay(attempt))
                    continue
                return _failure_message(
                    name=name,
                    call_id=call_id,
                    code="tool_timeout",
                    category="tool_runtime",
                    operation=name,
                    retryable=True,
                    attempt=attempt,
                    max_attempts=self._retry.max_attempts,
                    recovery="exhausted",
                    public_message="The financial tool timed out.",
                ), attempt
            except (ValidationError, TypeError, ValueError):
                return _failure_message(
                    name=name,
                    call_id=call_id,
                    code="malformed_tool_arguments",
                    category="tool_input",
                    operation=name,
                    retryable=False,
                    attempt=attempt,
                    max_attempts=self._retry.max_attempts,
                    recovery="model_correction",
                    public_message=(
                        "The tool arguments were invalid; correct them and retry."
                    ),
                ), attempt
            except FinancialProviderTimeout:
                if attempt < self._retry.max_attempts:
                    await self._sleeper(self._retry.delay(attempt))
                    continue
                return _failure_message(
                    name=name,
                    call_id=call_id,
                    code="provider_timeout",
                    category="provider",
                    operation=name,
                    retryable=True,
                    attempt=attempt,
                    max_attempts=self._retry.max_attempts,
                    recovery="exhausted",
                    public_message="Financial data is temporarily unavailable.",
                ), attempt
            except FinancialProviderError:
                if attempt < self._retry.max_attempts:
                    await self._sleeper(self._retry.delay(attempt))
                    continue
                return _failure_message(
                    name=name,
                    call_id=call_id,
                    code="provider_unavailable",
                    category="provider",
                    operation=name,
                    retryable=True,
                    attempt=attempt,
                    max_attempts=self._retry.max_attempts,
                    recovery="exhausted",
                    public_message="Financial data is temporarily unavailable.",
                ), attempt
            except Exception:
                return _failure_message(
                    name=name,
                    call_id=call_id,
                    code="tool_internal_error",
                    category="tool_runtime",
                    operation=name,
                    retryable=False,
                    attempt=attempt,
                    max_attempts=self._retry.max_attempts,
                    recovery="degraded",
                    public_message="The financial tool could not complete safely.",
                ), attempt


def _failure_message(
    *,
    name: str,
    call_id: str,
    code: str,
    category: str,
    operation: str,
    retryable: bool,
    attempt: int,
    max_attempts: int,
    recovery: str,
    public_message: str,
) -> ToolMessage:
    artifact_type = _artifact_type(name)
    failure = StructuredFailure(
        code=code,
        category=cast(FailureCategory, category),
        source="mini_alpha",
        operation=operation,
        retryable=retryable,
        attempt=attempt,
        max_attempts=max_attempts,
        recovery=cast(FailureRecovery, recovery),
        tool_call_id=call_id,
    )
    artifact = {
        "artifact_type": artifact_type,
        "schema_version": 1,
        "status": "error",
        "error": public_message,
        "failure": failure.to_dict(),
    }
    return ToolMessage(
        content=public_message,
        name=name,
        tool_call_id=call_id,
        status="error",
        artifact=artifact,
    )


def _artifact_type(tool_name: str) -> str:
    prefixes = {
        "get_": "",
        "calculate_": "",
        "analyze_": "",
        "backtest_": "",
    }
    value = tool_name
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value.removeprefix(prefix)
            break
    return value or "tool_failure"


def _is_error_artifact(message: ToolMessage) -> bool:
    return isinstance(message.artifact, dict) and message.artifact.get("status") == (
        "error"
    )


def _artifact_is_retryable(message: ToolMessage) -> bool:
    if not _is_error_artifact(message):
        return False
    assert isinstance(message.artifact, dict)
    failure = message.artifact.get("failure")
    return isinstance(failure, dict) and failure.get("retryable") is True


def _with_attempt_metadata(
    message: ToolMessage,
    *,
    attempt: int,
    max_attempts: int,
    recovery: FailureRecovery | None = None,
    tool_call_id: str,
) -> ToolMessage:
    """Return a copy whose safe failure metadata reflects executor attempts."""
    if not isinstance(message.artifact, dict):
        return message
    raw_failure = message.artifact.get("failure")
    if not isinstance(raw_failure, dict):
        return message
    parsed = StructuredFailure(
        code=str(raw_failure.get("code", "financial_data_error")),
        category=cast(
            FailureCategory,
            raw_failure.get("category", "tool_runtime"),
        ),
        source=str(raw_failure.get("source", "financial_tool")),
        operation=str(raw_failure.get("operation", message.name or "unknown")),
        retryable=raw_failure.get("retryable") is True,
        attempt=attempt,
        max_attempts=max_attempts,
        recovery=recovery
        or cast(
            FailureRecovery,
            raw_failure.get("recovery", "continue_without_tool"),
        ),
        tool_call_id=tool_call_id,
    )
    artifact = {**message.artifact, "failure": parsed.to_dict()}
    return message.model_copy(update={"artifact": artifact, "status": "error"})
