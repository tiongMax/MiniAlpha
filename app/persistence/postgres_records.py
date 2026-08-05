"""Conversion between PostgreSQL rows and persistence records."""

from collections.abc import Mapping
from datetime import datetime
from typing import Literal, cast
from uuid import UUID

from psycopg.types.json import Jsonb

from app.persistence.artifacts import parse_artifact
from app.persistence.models import (
    ConversationRun,
    ConversationThread,
    StoredArtifact,
)

RUN_COLUMNS = """
    r.conversation_response_id,
    r.conversation_query_id,
    r.conversation_thread_id,
    r.turn_index,
    r.attempt_no,
    r.request_key,
    r.status,
    q.content,
    r.answer,
    r.tool_calls,
    r.error_code,
    r.error_message,
    r.started_at,
    r.completed_at
"""


def artifact_values(
    ordinal: int,
    artifact: Mapping[str, object],
) -> tuple[int, str, int, str, Jsonb | None, str | None]:
    """Validate an artifact and build its SQL parameter tuple."""
    parsed = parse_artifact(artifact)
    return (
        ordinal,
        parsed.artifact_type,
        parsed.schema_version,
        parsed.status,
        Jsonb(parsed.data) if parsed.data is not None else None,
        parsed.error,
    )


def thread_from_row(row: Mapping[str, object]) -> ConversationThread:
    """Convert one thread row into its transport-neutral record."""
    return ConversationThread(
        thread_id=cast(UUID, row["conversation_thread_id"]),
        status=cast(
            Literal["in_progress", "completed", "error", "cancelled"],
            row["current_status"],
        ),
        title=cast(str | None, row["title"]),
        latest_checkpoint_id=cast(str | None, row["latest_checkpoint_id"]),
        next_turn_index=int(cast(int, row["next_turn_index"])),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
    )


def run_from_row(row: Mapping[str, object]) -> ConversationRun:
    """Convert a joined query/run row into its application record."""
    raw_calls = row["tool_calls"]
    calls = raw_calls if isinstance(raw_calls, list) else []
    return ConversationRun(
        run_id=cast(UUID, row["conversation_response_id"]),
        query_id=cast(UUID, row["conversation_query_id"]),
        thread_id=cast(UUID, row["conversation_thread_id"]),
        turn_index=int(cast(int, row["turn_index"])),
        attempt_no=int(cast(int, row["attempt_no"])),
        request_key=cast(UUID | None, row["request_key"]),
        status=cast(
            Literal["in_progress", "completed", "error", "cancelled"],
            row["status"],
        ),
        message=str(row["content"]),
        answer=cast(str | None, row["answer"]),
        tool_calls=tuple(
            cast(dict[str, object], call) for call in calls if isinstance(call, dict)
        ),
        error_code=cast(str | None, row["error_code"]),
        error_message=cast(str | None, row["error_message"]),
        started_at=cast(datetime, row["started_at"]),
        completed_at=cast(datetime | None, row["completed_at"]),
    )


def artifact_from_row(row: Mapping[str, object]) -> StoredArtifact:
    """Convert one artifact row into its application record."""
    data = row["data"]
    return StoredArtifact(
        artifact_id=cast(UUID, row["artifact_id"]),
        run_id=cast(UUID, row["conversation_response_id"]),
        ordinal=int(cast(int, row["ordinal"])),
        artifact_type=str(row["artifact_type"]),
        schema_version=int(cast(int, row["schema_version"])),
        status=cast(Literal["ok", "error"], row["status"]),
        data=cast(dict[str, object], data) if isinstance(data, dict) else None,
        error=cast(str | None, row["error"]),
        created_at=cast(datetime, row["created_at"]),
    )
