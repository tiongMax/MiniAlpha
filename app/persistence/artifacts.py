"""Validation shared by conversation artifact repositories."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from app.agent.failures import parse_structured_failure


@dataclass(frozen=True, slots=True)
class ParsedArtifact:
    """Validated fields ready for persistence."""

    artifact_type: str
    schema_version: int
    status: Literal["ok", "error"]
    data: dict[str, object] | None
    error: str | None
    failure: dict[str, object] | None


def parse_artifact(artifact: Mapping[str, object]) -> ParsedArtifact:
    """Validate one versioned artifact envelope before storing it."""
    artifact_type = artifact.get("artifact_type")
    if not isinstance(artifact_type, str) or not artifact_type:
        raise ValueError("Artifact type must be a non-empty string.")

    schema_version = artifact.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version < 1
    ):
        raise ValueError("Artifact schema version must be a positive integer.")

    raw_status = artifact.get("status")
    if raw_status == "ok":
        status: Literal["ok", "error"] = "ok"
    elif raw_status == "error":
        status = "error"
    else:
        raise ValueError("Artifact status must be ok or error.")

    raw_data = artifact.get("data")
    raw_error = artifact.get("error")
    raw_failure = artifact.get("failure")
    if status == "ok":
        if (
            not isinstance(raw_data, dict)
            or raw_error is not None
            or raw_failure is not None
        ):
            raise ValueError(
                "Successful artifacts require data and no error or failure."
            )
        data = cast(dict[str, object], raw_data)
        error = None
        failure = None
    else:
        if raw_data is not None or not isinstance(raw_error, str) or not raw_error:
            raise ValueError("Error artifacts require an error and no data.")
        data = None
        error = raw_error
        if raw_failure is None:
            failure = None
        elif isinstance(raw_failure, Mapping):
            failure = parse_structured_failure(raw_failure).to_dict()
        else:
            raise ValueError("Artifact failure must be a structured object.")

    return ParsedArtifact(
        artifact_type=artifact_type,
        schema_version=schema_version,
        status=status,
        data=data,
        error=error,
        failure=failure,
    )
