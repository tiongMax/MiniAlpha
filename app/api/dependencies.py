"""Application composition and FastAPI dependency access."""

import logging
from typing import cast

from fastapi import Request

from app.agent.graph import build_graph
from app.agent.tool_registry import ToolRegistry
from app.agent.tools import create_default_tools
from app.cache.runtime import CacheRuntime
from app.config import (
    create_model,
    get_boolean,
    get_database_url,
    get_redis_url,
    get_timeout_seconds,
)
from app.persistence.runtime import PersistenceRuntime
from app.services.research_agent import ResearchAgentService, ResearchGraph
from app.services.run_manager import DetachedRunManager
from app.services.thread_research import ThreadResearchService

logger = logging.getLogger(__name__)


class ResearchServiceUnavailableError(RuntimeError):
    """Raised when the application has no usable research service."""


class ThreadServiceUnavailableError(RuntimeError):
    """Raised when persistent thread research is unavailable."""


class RunManagerUnavailableError(RuntimeError):
    """Raised when detached execution is unavailable."""


async def create_research_service() -> tuple[ResearchAgentService, CacheRuntime | None]:
    """Compose the production model, tools, graph, and application service."""
    model = create_model()
    tools = list(create_default_tools())
    graph = build_graph(
        model,
        tools=tools,
        model_timeout_seconds=get_timeout_seconds("MODEL_TIMEOUT_SECONDS", 60),
        tool_timeout_seconds=get_timeout_seconds("TOOL_TIMEOUT_SECONDS", 30),
    )
    cache_runtime = None
    if get_boolean("RESEARCH_CACHE_ENABLED", True):
        try:
            api_key = str(model.google_api_key.get_secret_value())
            cache_runtime = await CacheRuntime.open(
                redis_url=get_redis_url(),
                database_url=get_database_url(),
                registry=ToolRegistry(tools),
                generation_model=str(model.model),
                api_key=api_key,
            )
        except Exception:
            logger.exception("Stateless result-cache composition failed open")
    return (
        ResearchAgentService(
            cast(ResearchGraph, graph),
            result_cache=cache_runtime.service if cache_runtime is not None else None,
        ),
        cache_runtime,
    )


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
            model_timeout_seconds=get_timeout_seconds("MODEL_TIMEOUT_SECONDS", 60),
            tool_timeout_seconds=get_timeout_seconds("TOOL_TIMEOUT_SECONDS", 30),
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


def get_run_manager(request: Request) -> DetachedRunManager:
    """Return the application-scoped detached execution manager."""
    manager = getattr(request.app.state, "run_manager", None)
    if not isinstance(manager, DetachedRunManager):
        raise RunManagerUnavailableError("Detached execution is unavailable.")
    return manager
