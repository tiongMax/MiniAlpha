"""Application composition and FastAPI dependency access."""

from typing import cast

from fastapi import Request

from app.agent.graph import build_graph
from app.config import create_model, get_database_url
from app.persistence.runtime import PersistenceRuntime
from app.services.research_agent import ResearchAgentService, ResearchGraph
from app.services.thread_research import ThreadResearchService


class ResearchServiceUnavailableError(RuntimeError):
    """Raised when the application has no usable research service."""


class ThreadServiceUnavailableError(RuntimeError):
    """Raised when persistent thread research is unavailable."""


def create_research_service() -> ResearchAgentService:
    """Compose the production model, tools, graph, and application service."""
    graph = build_graph(create_model())
    return ResearchAgentService(cast(ResearchGraph, graph))


async def create_thread_research_service() -> tuple[
    ThreadResearchService,
    PersistenceRuntime,
]:
    """Compose PostgreSQL, the checkpointed graph, and thread orchestration."""
    runtime = await PersistenceRuntime.open(get_database_url())
    try:
        graph = build_graph(
            create_model(),
            checkpointer=runtime.checkpointer,
        )
        agent = ResearchAgentService(cast(ResearchGraph, graph))
        return ThreadResearchService(runtime.repository, agent), runtime
    except Exception:
        await runtime.close()
        raise


def get_research_service(request: Request) -> ResearchAgentService:
    """Return the application-scoped research service."""
    service = getattr(request.app.state, "research_service", None)
    if not isinstance(service, ResearchAgentService):
        raise ResearchServiceUnavailableError("Research service is unavailable.")
    return service


def get_thread_research_service(request: Request) -> ThreadResearchService:
    """Return the application-scoped durable thread service."""
    service = getattr(request.app.state, "thread_research_service", None)
    if not isinstance(service, ThreadResearchService):
        raise ThreadServiceUnavailableError(
            "Persistent thread research is unavailable."
        )
    return service
