"""Stateless financial-research endpoint."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_research_service
from app.api.schemas import (
    ArtifactResponse,
    ErrorResponse,
    ResearchRequest,
    ResearchResponse,
    ToolCallResponse,
)
from app.services.research_agent import ResearchAgentService

router = APIRouter(prefix="/api/v1", tags=["research"])


@router.post(
    "/research",
    response_model=ResearchResponse,
    response_model_exclude_none=True,
    summary="Run stateless financial research",
    responses={
        502: {
            "model": ErrorResponse,
            "description": "The agent could not complete the request.",
        },
        503: {
            "model": ErrorResponse,
            "description": "The research service is not configured.",
        },
    },
)
async def research(
    request: ResearchRequest,
    service: Annotated[ResearchAgentService, Depends(get_research_service)],
) -> ResearchResponse:
    """Execute one request with no server-managed conversation history."""
    result = await service.research(request.message)
    return ResearchResponse(
        answer=result.answer,
        tool_calls=[
            ToolCallResponse(
                name=call.name,
                arguments=call.arguments,
                status=call.status,
                summary=call.summary,
            )
            for call in result.tool_calls
        ],
        artifacts=[
            ArtifactResponse.model_validate(artifact) for artifact in result.artifacts
        ],
    )
