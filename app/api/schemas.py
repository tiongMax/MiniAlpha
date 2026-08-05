"""Versioned HTTP request and response contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResearchRequest(BaseModel):
    """One stateless natural-language research request."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(
        min_length=1,
        max_length=10_000,
        description="Standalone financial-research question.",
        examples=["Compare Apple and Microsoft using verified company facts."],
    )

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        """Trim input and reject values containing only whitespace."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("message must not be blank")
        return normalized


class ToolCallResponse(BaseModel):
    """Tool invocation observed during graph execution."""

    name: str = Field(description="LangChain tool name selected by the model.")
    arguments: dict[str, object] = Field(
        description="Validated arguments supplied to the tool."
    )


class ArtifactResponse(BaseModel):
    """Structured artifact emitted by an agent tool."""

    model_config = ConfigDict(extra="allow")

    artifact_type: str = Field(
        description="Discriminator for the tool-specific artifact payload."
    )
    schema_version: int = Field(
        ge=1,
        description="Version of the artifact contract.",
    )
    status: Literal["ok", "error"] = Field(
        description="Whether the tool produced data or a controlled error."
    )
    data: dict[str, object] | None = Field(
        default=None,
        description="Normalized tool data when status is ok.",
    )
    error: str | None = Field(
        default=None,
        description="Safe error message when status is error.",
    )


class ResearchResponse(BaseModel):
    """Completed answer with its tool-call and artifact evidence."""

    answer: str = Field(description="Final model answer.")
    tool_calls: list[ToolCallResponse] = Field(
        description="Tool requests made while producing the answer."
    )
    artifacts: list[ArtifactResponse] = Field(
        description="Versioned evidence emitted by those tools."
    )


class HealthResponse(BaseModel):
    """Process-liveness response."""

    status: Literal["ok"]
    service: Literal["mini-alpha"]
    phase: Literal[8]


class ReadinessResponse(BaseModel):
    """Application readiness including persistent thread composition."""

    status: Literal["ready"]
    service: Literal["mini-alpha"]
    phase: Literal[8]
    persistence: Literal["ready"]


class ErrorDetail(BaseModel):
    """Stable machine-readable API error."""

    code: str = Field(description="Stable error identifier for clients.")
    message: str = Field(description="Safe human-readable explanation.")


class ErrorResponse(BaseModel):
    """Envelope used for controlled server errors."""

    error: ErrorDetail


class UserMessageRequest(BaseModel):
    """One new user message submitted to a durable thread."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user"] = Field(description="Only new user messages are accepted.")
    content: str = Field(min_length=1, max_length=10_000)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        """Trim input and reject whitespace-only content."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("message content must not be blank")
        return normalized


class ThreadMessageRequest(BaseModel):
    """Exactly one new message and an optional idempotency identity."""

    model_config = ConfigDict(extra="forbid")

    messages: list[UserMessageRequest] = Field(
        min_length=1,
        max_length=1,
        description="Exactly one new user message; history is server-managed.",
    )
    request_key: UUID | None = Field(
        default=None,
        description="Client-generated UUID reused across retransmissions.",
    )


class ThreadMessageResponse(BaseModel):
    """Completed durable research turn."""

    thread_id: UUID
    run_id: UUID
    turn_index: int = Field(ge=1)
    status: Literal["completed"]
    answer: str
    tool_calls: list[ToolCallResponse]
    artifacts: list[ArtifactResponse]
    replayed: bool


class ThreadResponse(BaseModel):
    """Public durable-thread metadata."""

    thread_id: UUID
    status: Literal["in_progress", "completed", "error", "cancelled"]
    title: str | None
    created_at: datetime
    updated_at: datetime


class ThreadListResponse(BaseModel):
    """Paginated durable-thread collection."""

    threads: list[ThreadResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class ThreadTurnResponse(BaseModel):
    """One durable transcript entry."""

    run_id: UUID
    turn_index: int = Field(ge=1)
    attempt_no: int = Field(ge=1)
    status: Literal["in_progress", "completed", "error", "cancelled"]
    message: str
    answer: str | None
    tool_calls: list[ToolCallResponse]
    artifacts: list[ArtifactResponse]
    error: ErrorDetail | None
    started_at: datetime
    completed_at: datetime | None


class ThreadTranscriptResponse(BaseModel):
    """Ordered durable turns for one thread."""

    thread_id: UUID
    turns: list[ThreadTurnResponse]


class RunAcceptedResponse(BaseModel):
    """Identity returned before detached execution completes."""

    run_id: UUID
    thread_id: UUID
    turn_index: int = Field(ge=1)
    status: Literal["in_progress", "completed", "error", "cancelled"]
    replayed: bool
    events_url: str


class RunCancellationResponse(BaseModel):
    """Durable result of an explicit cancellation request."""

    run_id: UUID
    thread_id: UUID
    status: Literal["cancelled"]
