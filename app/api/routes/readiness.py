"""Application readiness endpoint."""

from fastapi import APIRouter, Request

from app.api.dependencies import (
    ResearchServiceUnavailableError,
    RunManagerUnavailableError,
    ThreadServiceUnavailableError,
)
from app.api.schemas import ErrorResponse, ReadinessResponse
from app.persistence.runtime import PersistenceRuntime

router = APIRouter(tags=["system"])


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Check application and persistence readiness",
    responses={
        503: {
            "model": ErrorResponse,
            "description": "Required application dependencies are unavailable.",
        }
    },
)
async def readiness(request: Request) -> ReadinessResponse:
    """Verify composed research services and live persistence."""
    if getattr(request.app.state, "research_service", None) is None:
        raise ResearchServiceUnavailableError("Research service is unavailable.")
    if getattr(request.app.state, "thread_research_service", None) is None:
        raise ThreadServiceUnavailableError(
            "Persistent thread research is unavailable."
        )
    event_store = getattr(request.app.state, "event_store", None)
    if event_store is None or not await event_store.is_ready():
        raise RunManagerUnavailableError("Redis event transport is unavailable.")

    runtime = getattr(request.app.state, "persistence_runtime", None)
    if isinstance(runtime, PersistenceRuntime) and not await runtime.is_ready():
        raise ThreadServiceUnavailableError(
            "Persistent thread research is unavailable."
        )
    return ReadinessResponse(
        status="ready",
        service="mini-alpha",
        phase=8,
        persistence="ready",
    )
