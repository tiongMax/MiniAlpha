"""Stable HTTP error translation for MiniAlpha services."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.dependencies import (
    AuthServiceUnavailableError,
    MarketDataServiceUnavailableError,
    ResearchServiceUnavailableError,
    RunManagerUnavailableError,
    ThreadServiceUnavailableError,
)
from app.api.schemas import ErrorDetail, ErrorResponse
from app.auth.repository import AccountPersistenceError, EmailAlreadyRegisteredError
from app.auth.service import AuthenticationRequiredError, InvalidCredentialsError
from app.persistence.repository import (
    CheckpointConflictError,
    ConversationPersistenceError,
    RequestKeyConflictError,
    RunInProgressError,
    RunLifecycleConflictError,
    RunNotFoundError,
    ThreadNotFoundError,
)
from app.services.research_agent import ResearchExecutionError
from app.services.thread_research import (
    ExistingRunInProgressError,
    PersistedRunFailedError,
)

logger = logging.getLogger(__name__)


def error_response(
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


def register_exception_handlers(api: FastAPI) -> None:
    """Attach all controlled service-to-HTTP exception translations."""

    @api.exception_handler(ResearchExecutionError)
    async def handle_research_failure(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return error_response(
            status_code=502,
            code="research_failed",
            message="The research agent could not complete the request.",
        )

    @api.exception_handler(ResearchServiceUnavailableError)
    async def handle_unavailable_service(
        request: Request,
        _error: Exception,
    ) -> JSONResponse:
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
        return error_response(
            status_code=503,
            code="research_unavailable",
            message=message,
        )

    @api.exception_handler(ThreadServiceUnavailableError)
    @api.exception_handler(RunManagerUnavailableError)
    async def handle_unavailable_persistence(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return error_response(
            status_code=503,
            code="persistence_unavailable",
            message="Persistent research threads are unavailable.",
        )

    @api.exception_handler(MarketDataServiceUnavailableError)
    async def handle_unavailable_market_data(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return error_response(
            status_code=503,
            code="market_data_unavailable",
            message="Watchlist market data is unavailable.",
        )

    @api.exception_handler(AuthServiceUnavailableError)
    @api.exception_handler(AccountPersistenceError)
    async def handle_unavailable_auth(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return error_response(
            status_code=503,
            code="authentication_unavailable",
            message="Account services are temporarily unavailable.",
        )

    @api.exception_handler(AuthenticationRequiredError)
    async def handle_authentication_required(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        response = error_response(
            status_code=401,
            code="authentication_required",
            message="Sign in to continue.",
        )
        response.headers["WWW-Authenticate"] = "Session"
        return response

    @api.exception_handler(InvalidCredentialsError)
    async def handle_invalid_credentials(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return error_response(
            status_code=401,
            code="invalid_credentials",
            message="Email or password is incorrect.",
        )

    @api.exception_handler(EmailAlreadyRegisteredError)
    async def handle_registered_email(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return error_response(
            status_code=409,
            code="email_already_registered",
            message="An account already exists for that email address.",
        )

    @api.exception_handler(ThreadNotFoundError)
    async def handle_thread_not_found(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return error_response(
            status_code=404,
            code="thread_not_found",
            message="The research thread was not found.",
        )

    @api.exception_handler(RunNotFoundError)
    async def handle_run_not_found(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return error_response(
            status_code=404,
            code="run_not_found",
            message="The research run was not found.",
        )

    @api.exception_handler(RequestKeyConflictError)
    async def handle_request_key_conflict(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return error_response(
            status_code=409,
            code="request_key_conflict",
            message="The request key belongs to a different request.",
        )

    @api.exception_handler(RunInProgressError)
    @api.exception_handler(ExistingRunInProgressError)
    async def handle_run_in_progress(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return error_response(
            status_code=409,
            code="run_in_progress",
            message="The research thread already has an active run.",
        )

    @api.exception_handler(CheckpointConflictError)
    async def handle_checkpoint_conflict(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return error_response(
            status_code=409,
            code="thread_conflict",
            message="The research thread changed during execution.",
        )

    @api.exception_handler(RunLifecycleConflictError)
    async def handle_run_conflict(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return error_response(
            status_code=409,
            code="run_conflict",
            message="The research run is already terminal.",
        )

    @api.exception_handler(PersistedRunFailedError)
    async def handle_persisted_run_failure(
        _request: Request,
        error: PersistedRunFailedError,
    ) -> JSONResponse:
        return error_response(
            status_code=502,
            code=error.error_code,
            message=str(error),
        )

    @api.exception_handler(ConversationPersistenceError)
    async def handle_persistence_failure(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        return error_response(
            status_code=503,
            code="persistence_unavailable",
            message="Persistent research threads are unavailable.",
        )

    @api.exception_handler(Exception)
    async def handle_unexpected_failure(
        _request: Request,
        _error: Exception,
    ) -> JSONResponse:
        logger.exception("Unexpected API failure")
        return error_response(
            status_code=500,
            code="internal_error",
            message="An unexpected server error occurred.",
        )
