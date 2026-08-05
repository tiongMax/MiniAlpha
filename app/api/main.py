"""FastAPI application factory and production application instance."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.dependencies import (
    create_research_service,
    create_thread_research_service,
)
from app.api.errors import register_exception_handlers
from app.api.routes.health import router as health_router
from app.api.routes.readiness import router as readiness_router
from app.api.routes.research import router as research_router
from app.api.routes.runs import router as runs_router
from app.api.routes.threads import router as threads_router
from app.config import (
    get_positive_int,
    get_redis_url,
    get_timeout_seconds,
)
from app.events.store import (
    InMemoryRunEventStore,
    RedisRunEventStore,
    RunEventStore,
)
from app.services.research_agent import ResearchAgentService
from app.services.run_manager import DetachedRunManager
from app.services.thread_research import ThreadResearchService

logger = logging.getLogger(__name__)


def create_app(
    research_service: ResearchAgentService | None = None,
    thread_research_service: ThreadResearchService | None = None,
    event_store: RunEventStore | None = None,
) -> FastAPI:
    """Create the API with an injectable or production research service."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Compose production dependencies once while preserving liveness."""
        owned_runtime = None
        owned_event_store = None
        run_manager = None
        if research_service is not None:
            app.state.research_service = research_service
            app.state.research_startup_failed = False
            app.state.thread_research_service = thread_research_service
            app.state.persistence_runtime = None
            app.state.persistence_startup_failed = thread_research_service is None
        else:
            try:
                app.state.research_service = create_research_service()
                app.state.research_startup_failed = False
            except RuntimeError:
                app.state.research_service = None
                app.state.research_startup_failed = True
            try:
                (
                    app.state.thread_research_service,
                    owned_runtime,
                ) = await create_thread_research_service()
                app.state.persistence_runtime = owned_runtime
                app.state.persistence_startup_failed = False
            except Exception:
                logger.exception("Persistent research composition failed")
                app.state.thread_research_service = None
                app.state.persistence_runtime = None
                app.state.persistence_startup_failed = True
        if app.state.thread_research_service is not None:
            if event_store is not None:
                app.state.event_store = event_store
            elif research_service is not None:
                app.state.event_store = InMemoryRunEventStore()
            else:
                try:
                    owned_event_store = await RedisRunEventStore.open(
                        get_redis_url(),
                        retention_seconds=get_positive_int(
                            "RUN_EVENT_RETENTION_SECONDS", 86_400
                        ),
                    )
                    app.state.event_store = owned_event_store
                except Exception:
                    logger.exception("Redis event transport composition failed")
                    app.state.event_store = None
            if app.state.event_store is None:
                app.state.run_manager = None
                run_manager = None
            else:
                run_manager = DetachedRunManager(
                    app.state.thread_research_service,
                    event_store=app.state.event_store,
                    shutdown_grace_seconds=get_timeout_seconds(
                        "WORKER_SHUTDOWN_GRACE_SECONDS", 10
                    ),
                )
                await run_manager.start()
                app.state.run_manager = run_manager
        else:
            app.state.event_store = None
            app.state.run_manager = None
        try:
            yield
        finally:
            if run_manager is not None:
                await run_manager.close()
            if owned_event_store is not None:
                await owned_event_store.close()
            if owned_runtime is not None:
                await owned_runtime.close()

    api = FastAPI(
        title="MiniAlpha API",
        version="0.8.0",
        description=(
            "HTTP access to MiniAlpha's explicit LangGraph financial research "
            "agent. Use the stateless research route for independent requests "
            "or durable thread routes for PostgreSQL-backed conversation memory "
            "and live application-owned SSE events."
        ),
        lifespan=lifespan,
    )
    api.include_router(health_router)
    api.include_router(readiness_router)
    api.include_router(research_router)
    api.include_router(threads_router)
    api.include_router(runs_router)
    register_exception_handlers(api)

    @api.middleware("http")
    async def add_request_context(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Attach a correlation ID and log bounded request metadata."""
        request_id = str(uuid4())
        started_at = perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_id=%s method=%s path=%s status=%d duration_ms=%.1f",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            (perf_counter() - started_at) * 1000,
        )
        return response

    return api


app = create_app()
