"""Per-tool isolation, retry, and safe failure contracts."""

import asyncio

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from app.agent.retry import RetryPolicy
from app.agent.tool_executor import IsolatedToolExecutor
from app.domain.errors import FinancialProviderError


def calls(*items: dict[str, object]) -> dict[str, object]:
    return {"messages": [AIMessage(content="", tool_calls=list(items))]}


def test_slow_tool_does_not_cancel_successful_sibling() -> None:
    @tool(response_format="content_and_artifact")
    async def slow_tool() -> tuple[str, dict[str, object]]:
        """Synthetic timeout."""
        await asyncio.sleep(1)
        return "late", {"status": "ok"}

    @tool(response_format="content_and_artifact")
    async def fast_tool() -> tuple[str, dict[str, object]]:
        """Synthetic success."""
        return "ok", {
            "artifact_type": "fast",
            "schema_version": 1,
            "status": "ok",
            "data": {"value": 1},
        }

    executor = IsolatedToolExecutor(
        [slow_tool, fast_tool],
        timeout_seconds=0.001,
        retry_policy=RetryPolicy(max_attempts=1, base_delay_seconds=0),
    )
    result = asyncio.run(
        executor.ainvoke(
            calls(
                {
                    "name": "slow_tool",
                    "args": {},
                    "id": "slow",
                    "type": "tool_call",
                },
                {
                    "name": "fast_tool",
                    "args": {},
                    "id": "fast",
                    "type": "tool_call",
                },
            )
        )
    )

    by_id = {message.tool_call_id: message for message in result["messages"]}
    assert by_id["slow"].artifact["failure"]["code"] == "tool_timeout"
    assert by_id["fast"].artifact["status"] == "ok"


def test_transient_provider_failure_retries_once_then_succeeds() -> None:
    attempts = 0

    @tool(response_format="content_and_artifact")
    async def flaky_tool() -> tuple[str, dict[str, object]]:
        """Synthetic transient failure."""
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise FinancialProviderError("secret provider body")
        return "ok", {
            "artifact_type": "flaky",
            "schema_version": 1,
            "status": "ok",
            "data": {"value": 1},
        }

    executor = IsolatedToolExecutor(
        [flaky_tool],
        timeout_seconds=1,
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
    )
    result = asyncio.run(
        executor.ainvoke(
            calls(
                {
                    "name": "flaky_tool",
                    "args": {},
                    "id": "flaky",
                    "type": "tool_call",
                }
            )
        )
    )

    assert attempts == 2
    assert result["messages"][0].artifact["status"] == "ok"


def test_retryable_error_artifact_from_production_style_tool_is_retried() -> None:
    attempts = 0

    @tool(response_format="content_and_artifact")
    async def wrapped_provider_tool() -> tuple[str, dict[str, object]]:
        """Return the structured error shape emitted by production tools."""
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return "Unavailable.", {
                "artifact_type": "company_overview",
                "schema_version": 1,
                "status": "error",
                "error": "Unavailable.",
                "failure": {
                    "schema_version": 1,
                    "code": "provider_unavailable",
                    "category": "provider",
                    "source": "yahoo_finance",
                    "operation": "company_overview",
                    "retryable": True,
                    "attempt": 1,
                    "max_attempts": 1,
                    "recovery": "retry",
                },
            }
        return "ok", {
            "artifact_type": "company_overview",
            "schema_version": 1,
            "status": "ok",
            "data": {"symbol": "AAPL"},
        }

    executor = IsolatedToolExecutor(
        [wrapped_provider_tool],
        timeout_seconds=1,
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
    )
    result = asyncio.run(
        executor.ainvoke(
            calls(
                {
                    "name": "wrapped_provider_tool",
                    "args": {},
                    "id": "wrapped",
                    "type": "tool_call",
                }
            )
        )
    )

    assert attempts == 2
    assert result["messages"][0].artifact["status"] == "ok"


def test_persistent_retryable_artifact_reports_exhausted_attempt_budget() -> None:
    @tool(response_format="content_and_artifact")
    async def unavailable_tool() -> tuple[str, dict[str, object]]:
        """Return a persistent structured provider failure."""
        return "Unavailable.", {
            "artifact_type": "company_overview",
            "schema_version": 1,
            "status": "error",
            "error": "Unavailable.",
            "failure": {
                "schema_version": 1,
                "code": "provider_unavailable",
                "category": "provider",
                "source": "yahoo_finance",
                "operation": "company_overview",
                "retryable": True,
                "attempt": 1,
                "max_attempts": 1,
                "recovery": "retry",
            },
        }

    executor = IsolatedToolExecutor(
        [unavailable_tool],
        timeout_seconds=1,
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
    )
    result = asyncio.run(
        executor.ainvoke(
            calls(
                {
                    "name": "unavailable_tool",
                    "args": {},
                    "id": "persistent",
                    "type": "tool_call",
                }
            )
        )
    )

    failure = result["messages"][0].artifact["failure"]
    assert failure["attempt"] == 2
    assert failure["max_attempts"] == 2
    assert failure["recovery"] == "exhausted"
    assert failure["tool_call_id"] == "persistent"


def test_unknown_tool_and_bad_arguments_are_safe_correction_artifacts() -> None:
    @tool
    async def typed_tool(limit: int) -> str:
        """Require an integer."""
        return str(limit)

    executor = IsolatedToolExecutor(
        [typed_tool],
        timeout_seconds=1,
        retry_policy=RetryPolicy(max_attempts=2, base_delay_seconds=0),
    )
    result = asyncio.run(
        executor.ainvoke(
            calls(
                {
                    "name": "missing_tool",
                    "args": {},
                    "id": "missing",
                    "type": "tool_call",
                },
                {
                    "name": "typed_tool",
                    "args": {"limit": "not-an-int"},
                    "id": "bad",
                    "type": "tool_call",
                },
            )
        )
    )

    by_id = {message.tool_call_id: message for message in result["messages"]}
    assert by_id["missing"].artifact["failure"]["code"] == "unknown_tool"
    assert by_id["bad"].artifact["failure"]["code"] == ("malformed_tool_arguments")
    serialized = str(result)
    assert "not-an-int" not in serialized


def test_unexpected_exception_never_leaks_raw_message() -> None:
    @tool
    async def exploding_tool() -> str:
        """Raise a secret-bearing exception."""
        raise RuntimeError("token=super-secret https://private.example")

    executor = IsolatedToolExecutor(
        [exploding_tool], timeout_seconds=1, retry_policy=RetryPolicy(max_attempts=1)
    )
    result = asyncio.run(
        executor.ainvoke(
            calls(
                {
                    "name": "exploding_tool",
                    "args": {},
                    "id": "explode",
                    "type": "tool_call",
                }
            )
        )
    )

    serialized = str(result)
    assert "tool_internal_error" in serialized
    assert "super-secret" not in serialized
    assert "private.example" not in serialized
