"""Versioned HTTP request and response contracts."""

from typing import Literal

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
    phase: Literal[3]


class ErrorDetail(BaseModel):
    """Stable machine-readable API error."""

    code: str = Field(description="Stable error identifier for clients.")
    message: str = Field(description="Safe human-readable explanation.")


class ErrorResponse(BaseModel):
    """Envelope used for controlled server errors."""

    error: ErrorDetail
