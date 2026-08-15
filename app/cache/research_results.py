"""Validated serialization for complete stateless research results."""

from __future__ import annotations

from typing import Literal, cast

from app.services.research_agent import (
    ExecutedToolCall,
    ExecutedToolResult,
    ModelUsage,
    ResearchResult,
)

_SCHEMA_VERSION = 1


def serialize_research_result(result: ResearchResult) -> dict[str, object]:
    """Encode a completed result without Python-specific serialization."""
    return {
        "schema_version": _SCHEMA_VERSION,
        "answer": result.answer,
        "tool_calls": [
            {
                "name": call.name,
                "arguments": call.arguments,
                "status": call.status,
                "summary": call.summary,
            }
            for call in result.tool_calls
        ],
        "tool_results": [
            {
                "name": item.name,
                "content": item.content,
                "artifact": item.artifact,
            }
            for item in result.tool_results
        ],
        "artifacts": list(result.artifacts),
        "usage": {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "total_tokens": result.usage.total_tokens,
        },
    }


def deserialize_research_result(payload: dict[str, object]) -> ResearchResult:
    """Decode and strictly validate an untrusted cache payload."""
    if payload.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError("Unsupported cached research-result schema.")
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("Cached result requires a non-empty answer.")

    raw_calls = _object_list(payload, "tool_calls")
    calls: list[ExecutedToolCall] = []
    for raw in raw_calls:
        name = raw.get("name")
        arguments = raw.get("arguments")
        status = raw.get("status")
        summary = raw.get("summary")
        if not isinstance(name, str) or not name or not isinstance(arguments, dict):
            raise ValueError("Cached tool call is malformed.")
        if status not in (None, "ok", "error"):
            raise ValueError("Cached tool-call status is malformed.")
        if summary is not None and not isinstance(summary, str):
            raise ValueError("Cached tool-call summary is malformed.")
        calls.append(
            ExecutedToolCall(
                name=name,
                arguments=cast(dict[str, object], arguments),
                status=cast(Literal["ok", "error"] | None, status),
                summary=summary,
            )
        )

    raw_results = _object_list(payload, "tool_results")
    tool_results: list[ExecutedToolResult] = []
    for raw in raw_results:
        name = raw.get("name")
        content = raw.get("content")
        artifact = raw.get("artifact")
        if not isinstance(name, str) or not isinstance(content, str):
            raise ValueError("Cached tool result is malformed.")
        if artifact is not None and not isinstance(artifact, dict):
            raise ValueError("Cached tool-result artifact is malformed.")
        tool_results.append(
            ExecutedToolResult(
                name=name,
                content=content,
                artifact=cast(dict[str, object] | None, artifact),
            )
        )

    raw_artifacts = _object_list(payload, "artifacts")
    artifacts = tuple(cast(dict[str, object], item) for item in raw_artifacts)
    if any(item.get("status") != "ok" for item in artifacts):
        raise ValueError("Cached result contains a non-success artifact.")

    raw_usage = payload.get("usage")
    if not isinstance(raw_usage, dict):
        raise ValueError("Cached result requires usage metadata.")
    usage = ModelUsage(
        input_tokens=_token_value(raw_usage, "input_tokens"),
        output_tokens=_token_value(raw_usage, "output_tokens"),
        total_tokens=_token_value(raw_usage, "total_tokens"),
    )
    return ResearchResult(
        answer=answer,
        tool_calls=tuple(calls),
        tool_results=tuple(tool_results),
        artifacts=artifacts,
        checkpoint_id=None,
        usage=usage,
    )


def _object_list(payload: dict[str, object], key: str) -> list[dict[str, object]]:
    raw = payload.get(key)
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise ValueError(f"Cached result field {key!r} is malformed.")
    return cast(list[dict[str, object]], raw)


def _token_value(payload: dict[object, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("Cached usage metadata is malformed.")
    return value
