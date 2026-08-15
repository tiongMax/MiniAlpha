"""Tests for MiniAlpha's privacy-safe observability boundary."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import pytest

from app import observability
from app.observability import (
    RecordingSpanSink,
    SpanRecord,
    observe_span,
    reset_span_sink,
    set_span_sink,
)


@pytest.fixture(autouse=True)
def _reset_observability(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    reset_span_sink()
    yield
    reset_span_sink()


@contextmanager
def _recording_sink() -> Iterator[RecordingSpanSink]:
    sink = RecordingSpanSink()
    token = set_span_sink(sink)
    try:
        yield sink
    finally:
        reset_span_sink(token)


def test_recording_sink_captures_nested_parentage_and_attributes() -> None:
    with _recording_sink() as sink:
        with observe_span(
            "mini_alpha.research_run",
            metadata={"thread_id": "thread-1"},
            tags=("agent",),
        ) as parent:
            parent.set_attribute("route_count", 2)
            with observe_span(
                "routing.decision",
                metadata={"selected_tools": ["price_history", "company_news"]},
            ) as child:
                child.set_attributes({"candidate_count": 16, "fallback": False})

    by_name = {record.name: record for record in sink.spans}
    parent_record = by_name["mini_alpha.research_run"]
    child_record = by_name["routing.decision"]

    assert child_record.parent_span_id == parent_record.span_id
    assert parent_record.parent_span_id is None
    assert parent_record.attributes == {
        "thread_id": "[REDACTED]",
        "route_count": 2,
    }
    assert child_record.attributes["selected_tools"] == (
        "price_history",
        "company_news",
    )
    assert child_record.attributes["candidate_count"] == 16
    assert child_record.status == "ok"
    assert child_record.duration_ms >= 0
    assert parent_record.tags == ("agent",)


def test_sensitive_and_unbounded_metadata_is_safely_reduced() -> None:
    secret = "do not retain this application value"
    long_value = "x" * 300

    with _recording_sink() as sink:
        with observe_span(
            "tool.execute",
            run_type="tool",
            metadata={
                "prompt": secret,
                "tool_args": {"ticker": "AAPL"},
                "artifact.value": secret,
                "ticker": "AAPL",
                "api-key": "AIza" + "a" * 30,
                "input_tokens": 17,
                "provider": "yahoo",
                "long_value": long_value,
                "many": list(range(20)),
                "unknown": object(),
                "not_finite": float("inf"),
                "database": "postgresql://user:password@host/database",
            },
        ):
            pass

    attributes = sink.spans[0].attributes
    assert attributes["prompt"] == "[REDACTED]"
    assert attributes["tool_args"] == "[REDACTED]"
    assert attributes["artifact.value"] == "[REDACTED]"
    assert attributes["ticker"] == "[REDACTED]"
    assert attributes["api_key"] == "[REDACTED]"
    assert attributes["input_tokens"] == 17
    assert attributes["provider"] == "yahoo"
    assert attributes["long_value"].endswith("...")  # type: ignore[union-attr]
    assert len(attributes["long_value"]) == 160  # type: ignore[arg-type]
    assert attributes["many"] == (*range(12), "...")
    assert attributes["unknown"] == "[UNSUPPORTED]"
    assert attributes["not_finite"] == "[UNSUPPORTED]"
    assert attributes["database"] == "[REDACTED]"
    assert secret not in repr(sink.spans[0])


def test_exception_is_re_raised_and_only_its_class_is_recorded() -> None:
    sensitive_message = "raw provider response with customer portfolio"

    with _recording_sink() as sink:
        with pytest.raises(ValueError, match="customer portfolio"):
            with observe_span("provider.request", run_type="tool"):
                raise ValueError(sensitive_message)

    record = sink.spans[0]
    assert record.status == "error"
    assert record.error_type == "ValueError"
    assert sensitive_message not in repr(record)


def test_manual_error_marker_records_type_without_message() -> None:
    with _recording_sink() as sink:
        with observe_span("cache.semantic", run_type="retriever") as span:
            span.mark_error(TimeoutError("secret cache key"))

    assert sink.spans[0].status == "error"
    assert sink.spans[0].error_type == "TimeoutError"
    assert "secret cache key" not in repr(sink.spans[0])


class _ExplodingStartSink:
    def start_span(self, **kwargs: object) -> object:
        del kwargs
        raise RuntimeError("telemetry backend is unavailable")


class _ExplodingExport:
    def finish(self, record: SpanRecord) -> None:
        del record
        raise RuntimeError("telemetry flush failed")


class _ExplodingFinishSink:
    def start_span(self, **kwargs: object) -> _ExplodingExport:
        del kwargs
        return _ExplodingExport()


@pytest.mark.parametrize("sink", [_ExplodingStartSink(), _ExplodingFinishSink()])
def test_sink_failures_never_change_application_results(sink: object) -> None:
    token = set_span_sink(sink)  # type: ignore[arg-type]
    try:
        with observe_span("model.invoke", run_type="llm"):
            result = 42
    finally:
        reset_span_sink(token)

    assert result == 42


def test_sink_override_token_restores_previous_sink() -> None:
    first = RecordingSpanSink()
    second = RecordingSpanSink()
    first_token = set_span_sink(first)
    second_token = set_span_sink(second)
    reset_span_sink(second_token)
    try:
        with observe_span("cache.exact"):
            pass
    finally:
        reset_span_sink(first_token)

    assert [span.name for span in first.spans] == ["cache.exact"]
    assert second.spans == ()


def test_disabled_default_does_not_start_langsmith(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_trace(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("LangSmith must stay disabled")

    monkeypatch.setattr(observability, "_langsmith_trace", unexpected_trace)

    with observe_span("mini_alpha.research_run"):
        pass


class _FakeRun:
    def __init__(self) -> None:
        self.end_kwargs: dict[str, object] | None = None

    def end(self, **kwargs: object) -> None:
        self.end_kwargs = dict(kwargs)


class _FakeTraceContext:
    def __init__(self) -> None:
        self.run = _FakeRun()
        self.exited = False

    def __enter__(self) -> _FakeRun:
        return self.run

    def __exit__(self, *args: object) -> None:
        del args
        self.exited = True


def test_langsmith_export_uses_empty_io_and_sanitized_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    context = _FakeTraceContext()

    def fake_trace(name: str, **kwargs: object) -> _FakeTraceContext:
        captured.update({"name": name, **kwargs})
        return context

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setattr(observability, "_langsmith_trace", fake_trace)
    reset_span_sink()

    with observe_span(
        "model.invoke",
        run_type="llm",
        metadata={"model": "gemini-test", "prompt": "private question"},
    ) as span:
        span.set_attribute("output_tokens", 23)

    assert captured["name"] == "model.invoke"
    assert captured["inputs"] == {}
    assert captured["metadata"] == {
        "model": "gemini-test",
        "prompt": "[REDACTED]",
    }
    assert isinstance(captured["run_id"], type(__import__("uuid").uuid4()))
    assert context.run.end_kwargs == {
        "outputs": {},
        "error": None,
        "metadata": {
            "model": "gemini-test",
            "prompt": "[REDACTED]",
            "output_tokens": 23,
        },
    }
    assert context.exited is True


def test_attribute_count_and_names_are_bounded() -> None:
    metadata: Mapping[str, object] = {f"key-{index}": index for index in range(100)}

    with _recording_sink() as sink:
        with observe_span(
            " invalid span name / with data ",
            run_type="not-a-langsmith-type",
            metadata=metadata,
            tags=(f"tag {index}" for index in range(30)),
        ):
            pass

    record = sink.spans[0]
    assert record.name == "invalid_span_name_with_data"
    assert record.run_type == "chain"
    assert len(record.attributes) == 64
    assert len(record.tags) == 16
