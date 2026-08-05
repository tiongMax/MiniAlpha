"""Detached run admission, event attachment, and cancellation routes."""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_run_manager
from app.api.schemas import (
    ErrorResponse,
    RunAcceptedResponse,
    RunCancellationResponse,
    ThreadMessageRequest,
)
from app.api.sse import SSE_HEADERS, encode_sse
from app.services.run_manager import DetachedRunManager, RunSubmission

router = APIRouter(prefix="/api/v1", tags=["runs"])
RunManager = Annotated[DetachedRunManager, Depends(get_run_manager)]


def _accepted(submission: RunSubmission) -> RunAcceptedResponse:
    return RunAcceptedResponse(
        run_id=submission.run_id,
        thread_id=submission.thread_id,
        turn_index=submission.turn_index,
        status=submission.status,
        replayed=submission.replayed,
        events_url=f"/api/v1/runs/{submission.run_id}/events",
    )


async def _submit(
    request: ThreadMessageRequest,
    manager: DetachedRunManager,
    *,
    thread_id: UUID | None,
) -> RunAcceptedResponse:
    submission = await manager.submit(
        request.messages[0].content,
        thread_id=thread_id,
        request_key=request.request_key,
    )
    return _accepted(submission)


@router.post(
    "/threads/runs",
    response_model=RunAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def create_thread_run(
    request: ThreadMessageRequest,
    manager: RunManager,
) -> RunAcceptedResponse:
    """Create a thread, admit its first run, and detach execution."""
    return await _submit(request, manager, thread_id=None)


@router.post(
    "/threads/{thread_id}/runs",
    response_model=RunAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def continue_thread_run(
    thread_id: UUID,
    request: ThreadMessageRequest,
    manager: RunManager,
) -> RunAcceptedResponse:
    """Admit a detached continuation of an existing thread."""
    return await _submit(request, manager, thread_id=thread_id)


@router.get(
    "/runs/{run_id}/events",
    response_class=StreamingResponse,
    responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def attach_run_events(
    run_id: UUID,
    manager: RunManager,
) -> StreamingResponse:
    """Attach to buffered and future events without owning execution."""
    iterator = manager.events(run_id)
    first = await anext(iterator)

    async def frames() -> AsyncIterator[str]:
        yield encode_sse(first)
        async for event in iterator:
            yield encode_sse(event)

    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post(
    "/runs/{run_id}/cancel",
    response_model=RunCancellationResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def cancel_run(
    run_id: UUID,
    manager: RunManager,
) -> RunCancellationResponse:
    """Cancel an admitted run independently of any SSE connection."""
    run = await manager.cancel(run_id)
    return RunCancellationResponse(
        run_id=run.run_id,
        thread_id=run.thread_id,
        status="cancelled",
    )
