"""Privacy-safe, fail-open observability spans for MiniAlpha.

Application code records only operational metadata through :func:`observe_span`.
The default sink exports to LangSmith when tracing is explicitly enabled and is
otherwise a no-op. Tests and offline evaluations can install a
:class:`RecordingSpanSink` without credentials or network access.
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

try:
    from langsmith import trace as _langsmith_trace
except ImportError:  # pragma: no cover - production dependency, defensive import
    _langsmith_trace = None


type SpanStatus = Literal["ok", "error"]
type SafeScalar = str | int | float | bool | None
type SafeValue = SafeScalar | tuple[SafeScalar, ...]

_MAX_ATTRIBUTES = 64
_MAX_KEY_LENGTH = 64
_MAX_STRING_LENGTH = 160
_MAX_LIST_ITEMS = 12
_MAX_TAGS = 16
_MAX_TAG_LENGTH = 48
_MAX_SPAN_NAME_LENGTH = 96
_REDACTED = "[REDACTED]"
_UNSUPPORTED = "[UNSUPPORTED]"
_TRUNCATED_SUFFIX = "..."

_EXACT_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "arguments",
    "args",
    "authorization",
    "bearer",
    "content",
    "cookie",
    "credentials",
    "input",
    "inputs",
    "kwargs",
    "message",
    "messages",
    "output",
    "outputs",
    "password",
    "payload",
    "checkpoint_id",
    "prompt",
    "prompts",
    "query",
    "question",
    "raw",
    "refresh_token",
    "request_body",
    "request_id",
    "response_body",
    "run_id",
    "secret",
    "session_token",
    "token",
    "tool_args",
    "thread_id",
    "ticker",
    "url",
    "user_id",
    "symbol",
}
_SENSITIVE_KEY_TOKENS = {
    "argument",
    "arguments",
    "artifact",
    "authorization",
    "content",
    "cookie",
    "credential",
    "kwargs",
    "message",
    "messages",
    "password",
    "payload",
    "prompt",
    "prompts",
    "query",
    "question",
    "secret",
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(?:postgres(?:ql)?|redis)://[^\s]+"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bsk-[0-9A-Za-z_-]{12,}\b"),
)
_SAFE_NAME_PATTERN = re.compile(r"[^a-zA-Z0-9_.:-]+")
_SUPPORTED_RUN_TYPES = {
    "chain",
    "embedding",
    "llm",
    "parser",
    "prompt",
    "retriever",
    "tool",
}
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class SpanRecord:
    """A completed, privacy-sanitized span captured by a sink."""

    name: str
    run_type: str
    span_id: str
    parent_span_id: str | None
    status: SpanStatus
    attributes: Mapping[str, SafeValue]
    tags: tuple[str, ...]
    started_at: datetime
    ended_at: datetime
    duration_ms: float
    error_type: str | None = None


class SpanExport(Protocol):
    """A sink-owned handle for one in-progress span."""

    def finish(self, record: SpanRecord) -> None:
        """Export the completed span record."""


class SpanSink(Protocol):
    """Backend boundary used by :func:`observe_span`."""

    def start_span(
        self,
        *,
        name: str,
        run_type: str,
        span_id: str,
        parent_span_id: str | None,
        attributes: Mapping[str, SafeValue],
        tags: tuple[str, ...],
    ) -> SpanExport:
        """Open an exporter for a sanitized span."""


class _NoopExport:
    def finish(self, record: SpanRecord) -> None:
        del record


class _NoopSpanSink:
    def start_span(
        self,
        *,
        name: str,
        run_type: str,
        span_id: str,
        parent_span_id: str | None,
        attributes: Mapping[str, SafeValue],
        tags: tuple[str, ...],
    ) -> SpanExport:
        del name, run_type, span_id, parent_span_id, attributes, tags
        return _NOOP_EXPORT


class _RecordingExport:
    def __init__(self, sink: RecordingSpanSink) -> None:
        self._sink = sink

    def finish(self, record: SpanRecord) -> None:
        self._sink._append(record)


class RecordingSpanSink:
    """In-memory sink for deterministic tests and credential-free evaluations."""

    def __init__(self) -> None:
        self._records: list[SpanRecord] = []
        self._lock = threading.Lock()

    @property
    def spans(self) -> tuple[SpanRecord, ...]:
        """Return a stable snapshot of completed spans in completion order."""
        with self._lock:
            return tuple(self._records)

    def clear(self) -> None:
        """Discard previously recorded spans."""
        with self._lock:
            self._records.clear()

    def start_span(
        self,
        *,
        name: str,
        run_type: str,
        span_id: str,
        parent_span_id: str | None,
        attributes: Mapping[str, SafeValue],
        tags: tuple[str, ...],
    ) -> SpanExport:
        del name, run_type, span_id, parent_span_id, attributes, tags
        return _RecordingExport(self)

    def _append(self, record: SpanRecord) -> None:
        with self._lock:
            self._records.append(record)


class _LangSmithExport:
    def __init__(self, trace_context: object, run: object) -> None:
        self._trace_context = trace_context
        self._run = run

    def finish(self, record: SpanRecord) -> None:
        error = f"error_type:{record.error_type}" if record.error_type else None
        try:
            self._run.end(  # type: ignore[attr-defined]
                outputs={},
                error=error,
                metadata=dict(record.attributes),
            )
        finally:
            self._trace_context.__exit__(None, None, None)  # type: ignore[attr-defined]


class _LangSmithSpanSink:
    """Minimal LangSmith adapter that never sends application data values."""

    def start_span(
        self,
        *,
        name: str,
        run_type: str,
        span_id: str,
        parent_span_id: str | None,
        attributes: Mapping[str, SafeValue],
        tags: tuple[str, ...],
    ) -> SpanExport:
        del parent_span_id  # LangSmith links nested ``trace`` contexts itself.
        if _langsmith_trace is None:
            return _NOOP_EXPORT
        trace_context = _langsmith_trace(
            name,
            run_type=run_type,
            inputs={},
            metadata=dict(attributes),
            tags=list(tags),
            run_id=UUID(span_id),
            exceptions_to_handle=(Exception,),
        )
        run = trace_context.__enter__()
        return _LangSmithExport(trace_context, run)


class Span:
    """Mutable, privacy-sanitizing handle yielded by :func:`observe_span`."""

    __slots__ = ("_attributes", "_error_type", "span_id")

    def __init__(
        self,
        span_id: str,
        attributes: Mapping[str, SafeValue] | None = None,
    ) -> None:
        self.span_id = span_id
        self._attributes: dict[str, SafeValue] = dict(attributes or {})
        self._error_type: str | None = None

    def set_attribute(self, key: str, value: object) -> None:
        """Add one bounded attribute, redacting values under sensitive keys."""
        if len(self._attributes) >= _MAX_ATTRIBUTES and key not in self._attributes:
            return
        sanitized_key = _sanitize_key(key)
        if not sanitized_key:
            return
        self._attributes[sanitized_key] = _sanitize_attribute_value(
            sanitized_key,
            value,
        )

    def set_attributes(self, attributes: Mapping[str, object]) -> None:
        """Add multiple bounded attributes through the same privacy filter."""
        for key, value in attributes.items():
            self.set_attribute(key, value)

    def mark_error(self, error: BaseException) -> None:
        """Mark failure by exception class only; exception messages are discarded."""
        self._error_type = _sanitize_error_type(type(error).__name__)

    def mark_error_type(self, error_type: str) -> None:
        """Mark a controlled failure whose stable type is already available."""
        self._error_type = _sanitize_error_type(error_type)

    @property
    def attributes(self) -> Mapping[str, SafeValue]:
        """Return a copy of the currently sanitized attributes."""
        return dict(self._attributes)

    @property
    def error_type(self) -> str | None:
        """Return the safe exception class name, if marked failed."""
        return self._error_type


_NOOP_EXPORT = _NoopExport()
_NOOP_SINK = _NoopSpanSink()
_LANGSMITH_SINK = _LangSmithSpanSink()
_sink_override: ContextVar[SpanSink | None] = ContextVar(
    "mini_alpha_span_sink",
    default=None,
)
_active_span_id: ContextVar[str | None] = ContextVar(
    "mini_alpha_active_span_id",
    default=None,
)


def set_span_sink(sink: SpanSink) -> Token[SpanSink | None]:
    """Install a sink in the current context and return its reset token."""
    return _sink_override.set(sink)


def reset_span_sink(token: Token[SpanSink | None] | None = None) -> None:
    """Restore a previous sink token, or return to environment-driven defaults."""
    if token is None:
        _sink_override.set(None)
    else:
        _sink_override.reset(token)


@contextmanager
def observe_span(
    name: str,
    *,
    run_type: str = "chain",
    metadata: Mapping[str, object] | None = None,
    tags: Iterable[str] = (),
) -> Iterator[Span]:
    """Record one fail-open operational span without application data values.

    Sink startup and completion failures are deliberately swallowed so telemetry
    can never change the result of financial research. Exceptions raised by the
    instrumented operation are marked by class name and re-raised unchanged.
    """
    safe_name = _sanitize_span_name(name)
    safe_run_type = _sanitize_run_type(run_type)
    safe_attributes = _sanitize_attributes(metadata or {})
    safe_tags = _sanitize_tags(tags)
    span_id = str(uuid4())
    parent_span_id = _active_span_id.get()
    span = Span(span_id, safe_attributes)
    sink = _current_sink()
    try:
        export = sink.start_span(
            name=safe_name,
            run_type=safe_run_type,
            span_id=span_id,
            parent_span_id=parent_span_id,
            attributes=safe_attributes,
            tags=safe_tags,
        )
    except Exception:
        export = _NOOP_EXPORT

    started_at = datetime.now(UTC)
    started_ns = time.perf_counter_ns()
    active_token = _active_span_id.set(span_id)
    try:
        yield span
    except BaseException as error:
        span.mark_error(error)
        raise
    finally:
        _active_span_id.reset(active_token)
        ended_ns = time.perf_counter_ns()
        ended_at = datetime.now(UTC)
        record = SpanRecord(
            name=safe_name,
            run_type=safe_run_type,
            span_id=span_id,
            parent_span_id=parent_span_id,
            status="error" if span.error_type else "ok",
            attributes=dict(span.attributes),
            tags=safe_tags,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=max(0.0, (ended_ns - started_ns) / 1_000_000),
            error_type=span.error_type,
        )
        try:
            export.finish(record)
        except Exception:
            pass


def _current_sink() -> SpanSink:
    override = _sink_override.get()
    if override is not None:
        return override
    return _LANGSMITH_SINK if _langsmith_tracing_enabled() else _NOOP_SINK


def _langsmith_tracing_enabled() -> bool:
    raw_value = os.getenv(
        "LANGSMITH_TRACING",
        os.getenv("LANGCHAIN_TRACING_V2", "false"),
    )
    return raw_value.strip().casefold() in _TRUTHY


def _sanitize_attributes(
    attributes: Mapping[str, object],
) -> dict[str, SafeValue]:
    sanitized: dict[str, SafeValue] = {}
    for key, value in attributes.items():
        if len(sanitized) >= _MAX_ATTRIBUTES:
            break
        safe_key = _sanitize_key(key)
        if not safe_key:
            continue
        sanitized[safe_key] = _sanitize_attribute_value(safe_key, value)
    return sanitized


def _sanitize_key(key: object) -> str:
    if not isinstance(key, str):
        return ""
    normalized = key.strip().casefold().replace("-", "_").replace(" ", "_")
    normalized = _SAFE_NAME_PATTERN.sub("_", normalized)
    return normalized[:_MAX_KEY_LENGTH].strip("_")


def _sanitize_attribute_value(key: str, value: object) -> SafeValue:
    if _is_sensitive_key(key):
        if (
            key.endswith("_count")
            and isinstance(value, int)
            and not isinstance(value, bool)
        ):
            return value
        return _REDACTED
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        items = [_sanitize_scalar(item) for item in list(value)[:_MAX_LIST_ITEMS]]
        if len(value) > _MAX_LIST_ITEMS:
            items.append(_TRUNCATED_SUFFIX)
        return tuple(items)
    return _sanitize_scalar(value)


def _sanitize_scalar(value: object) -> SafeScalar:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else _UNSUPPORTED
    if not isinstance(value, str):
        return _UNSUPPORTED
    if any(pattern.search(value) for pattern in _SECRET_VALUE_PATTERNS):
        return _REDACTED
    if len(value) <= _MAX_STRING_LENGTH:
        return value
    return value[: _MAX_STRING_LENGTH - len(_TRUNCATED_SUFFIX)] + _TRUNCATED_SUFFIX


def _is_sensitive_key(key: str) -> bool:
    normalized = key.casefold()
    key_tokens = set(re.split(r"[._:-]+", normalized))
    return normalized in _EXACT_SENSITIVE_KEYS or bool(
        key_tokens & _SENSITIVE_KEY_TOKENS
    )


def _sanitize_tags(tags: Iterable[str]) -> tuple[str, ...]:
    sanitized: list[str] = []
    for tag in tags:
        if len(sanitized) >= _MAX_TAGS:
            break
        if not isinstance(tag, str):
            continue
        clean = _SAFE_NAME_PATTERN.sub("_", tag.strip())[:_MAX_TAG_LENGTH].strip("_")
        if clean and not _is_sensitive_key(clean):
            sanitized.append(clean)
    return tuple(sanitized)


def _sanitize_span_name(name: str) -> str:
    if not isinstance(name, str):
        return "mini_alpha.invalid_span"
    sanitized = _SAFE_NAME_PATTERN.sub("_", name.strip())
    sanitized = sanitized[:_MAX_SPAN_NAME_LENGTH].strip("_")
    return sanitized or "mini_alpha.invalid_span"


def _sanitize_run_type(run_type: str) -> str:
    normalized = run_type.strip().casefold() if isinstance(run_type, str) else ""
    return normalized if normalized in _SUPPORTED_RUN_TYPES else "chain"


def _sanitize_error_type(error_type: str) -> str:
    sanitized = _SAFE_NAME_PATTERN.sub("_", error_type)
    return sanitized[:_MAX_KEY_LENGTH] or "Exception"


__all__ = [
    "RecordingSpanSink",
    "Span",
    "SpanRecord",
    "SpanSink",
    "observe_span",
    "reset_span_sink",
    "set_span_sink",
]
