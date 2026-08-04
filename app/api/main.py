"""FastAPI application factory and production application instance."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from app.api.dependencies import (
    ResearchServiceUnavailableError,
    ThreadServiceUnavailableError,
    create_research_service,
    create_thread_research_service,
)
from app.api.routes.health import router as health_router
from app.api.routes.readiness import router as readiness_router
from app.api.routes.research import router as research_router
from app.api.schemas import ErrorDetail, ErrorResponse
from app.services.research_agent import (
    ResearchAgentService,
    ResearchExecutionError,
)
from app.services.thread_research import ThreadResearchService

logger = logging.getLogger(__name__)


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    """Build the stable JSON error envelope used by API handlers."""
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(),
    )


def create_app(
    research_service: ResearchAgentService | None = None,
    thread_research_service: ThreadResearchService | None = None,
) -> FastAPI:
    """Create the API with an injectable or production research service."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Compose production dependencies once while preserving liveness."""
        owned_runtime = None
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
        try:
            yield
        finally:
            if owned_runtime is not None:
                await owned_runtime.close()

    api = FastAPI(
        title="MiniAlpha API",
        version="0.2.0",
        description=(
            "Stateless HTTP access to MiniAlpha's explicit LangGraph financial "
            "research agent. Each request starts with fresh conversation state."
        ),
        lifespan=lifespan,
    )
    api.include_router(health_router)
    api.include_router(readiness_router)
    api.include_router(research_router)

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

    @api.exception_handler(ResearchExecutionError)
    async def handle_research_failure(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        """Translate agent execution failure into a safe gateway error."""
        return _error_response(
            status_code=502,
            code="research_failed",
            message="The research agent could not complete the request.",
        )

    @api.exception_handler(ResearchServiceUnavailableError)
    async def handle_unavailable_service(
        request: Request,
        _error: Exception,
    ) -> JSONResponse:
        """Report whether research failed during startup or later access."""
        startup_failed = getattr(
            request.app.state,
            "research_startup_failed",
            False,
        )
        message = (
            "The research service is not configured."
            if startup_failed
            else "The research service is unavailable."
        )
        return _error_response(
            status_code=503,
            code="research_unavailable",
            message=message,
        )

    @api.exception_handler(ThreadServiceUnavailableError)
    async def handle_unavailable_persistence(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        """Report unavailable persistent conversation composition."""
        return _error_response(
            status_code=503,
            code="persistence_unavailable",
            message="Persistent research threads are unavailable.",
        )

    @api.exception_handler(Exception)
    async def handle_unexpected_failure(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        """Contain unexpected failures without exposing internal details."""
        logger.exception("Unexpected API failure")
        return _error_response(
            status_code=500,
            code="internal_error",
            message="An unexpected server error occurred.",
        )

    return api


app = create_app()
