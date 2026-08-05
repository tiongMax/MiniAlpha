"""Liveness endpoint."""

from fastapi import APIRouter

from app.api.schemas import HealthResponse

router = APIRouter(tags=["system"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Check process liveness",
)
async def health() -> HealthResponse:
    """Report process liveness without calling external services."""
    return HealthResponse(status="ok", service="mini-alpha", phase=10)
