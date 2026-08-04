"""Application composition and FastAPI dependency access."""

from typing import cast

from fastapi import Request

from app.agent.graph import build_graph
from app.config import create_model
from app.services.research_agent import ResearchAgentService, ResearchGraph


class ResearchServiceUnavailableError(RuntimeError):
    """Raised when the application has no usable research service."""


def create_research_service() -> ResearchAgentService:
    """Compose the production model, tools, graph, and application service."""
    graph = build_graph(create_model())
    return ResearchAgentService(cast(ResearchGraph, graph))


def get_research_service(request: Request) -> ResearchAgentService:
    """Return the application-scoped research service."""
    service = getattr(request.app.state, "research_service", None)
    if not isinstance(service, ResearchAgentService):
        raise ResearchServiceUnavailableError("Research service is unavailable.")
    return service
