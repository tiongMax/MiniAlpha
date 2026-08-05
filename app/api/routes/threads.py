"""Synchronous and streaming durable thread message endpoints."""

from collections.abc import AsyncIterator
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_run_manager, get_thread_research_service
from app.api.schemas import (
    ArtifactResponse,
    ErrorDetail,
    ErrorResponse,
    ThreadListResponse,
    ThreadMessageRequest,
    ThreadMessageResponse,
    ThreadResponse,
    ThreadTranscriptResponse,
    ThreadTurnResponse,
    ToolCallResponse,
)
from app.api.sse import SSE_HEADERS, encode_sse
from app.persistence.models import ConversationThread, ConversationTurn
from app.services.run_manager import DetachedRunManager
from app.services.thread_research import ThreadResearchResult, ThreadResearchService

router = APIRouter(prefix="/api/v1/threads", tags=["threads"])
ThreadService = Annotated[
    ThreadResearchService,
    Depends(get_thread_research_service),
]
RunManager = Annotated[DetachedRunManager, Depends(get_run_manager)]


def _thread_response(thread: ConversationThread) -> ThreadResponse:
    return ThreadResponse(
        thread_id=thread.thread_id,
        status=thread.status,
        title=thread.title,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


def _message_response(result: ThreadResearchResult) -> ThreadMessageResponse:
    return ThreadMessageResponse(
        thread_id=result.thread_id,
        run_id=result.run_id,
        turn_index=result.turn_index,
        status="completed",
        answer=result.answer,
        tool_calls=[
            ToolCallResponse(name=call.name, arguments=call.arguments)
            for call in result.tool_calls
        ],
        artifacts=[
            ArtifactResponse.model_validate(artifact) for artifact in result.artifacts
        ],
        replayed=result.replayed,
    )


def _stored_tool_call_response(
    call: dict[str, object],
) -> ToolCallResponse:
    """Narrow one JSON-backed tool call into its public contract."""
    raw_arguments = call.get("arguments")
    arguments = (
        cast(dict[str, object], raw_arguments)
        if isinstance(raw_arguments, dict)
        else {}
    )
    return ToolCallResponse(
        name=str(call.get("name", "")),
        arguments=arguments,
    )


def _turn_response(turn: ConversationTurn) -> ThreadTurnResponse:
    error = None
    if turn.run.error_code is not None and turn.run.error_message is not None:
        error = ErrorDetail(
            code=turn.run.error_code,
            message=turn.run.error_message,
        )
    return ThreadTurnResponse(
        run_id=turn.run.run_id,
        turn_index=turn.run.turn_index,
        attempt_no=turn.run.attempt_no,
        status=turn.run.status,
        message=turn.run.message,
        answer=turn.run.answer,
        tool_calls=[_stored_tool_call_response(call) for call in turn.run.tool_calls],
        artifacts=[
            ArtifactResponse(
                artifact_type=artifact.artifact_type,
                schema_version=artifact.schema_version,
                status=artifact.status,
                data=artifact.data,
                error=artifact.error,
            )
            for artifact in turn.artifacts
        ],
        error=error,
        started_at=turn.run.started_at,
        completed_at=turn.run.completed_at,
    )


@router.post(
    "/messages",
    response_model=ThreadMessageResponse,
    response_model_exclude_none=True,
    summary="Create a thread and run its first message",
    responses={
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def create_thread_message(
    request: ThreadMessageRequest,
    response: Response,
    service: ThreadService,
) -> ThreadMessageResponse:
    """Create a durable thread with its first synchronous turn."""
    result = await service.research(
        request.messages[0].content,
        thread_id=None,
        request_key=request.request_key,
    )
    response.headers["Content-Location"] = (
        f"/api/v1/threads/{result.thread_id}/messages"
    )
    return _message_response(result)


async def _stream_response(
    request: ThreadMessageRequest,
    manager: DetachedRunManager,
    *,
    thread_id: UUID | None,
) -> StreamingResponse:
    """Admit one run, then expose its stable application events as SSE."""
    submission = await manager.submit(
        request.messages[0].content,
        thread_id=thread_id,
        request_key=request.request_key,
    )

    async def frames() -> AsyncIterator[str]:
        async for event in manager.events(submission.run_id):
            yield encode_sse(event)

    actual_thread_id = submission.thread_id
    headers = {
        **SSE_HEADERS,
        "Content-Location": f"/api/v1/threads/{actual_thread_id}/messages",
    }
    return StreamingResponse(
        frames(),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post(
    "/messages/stream",
    response_class=StreamingResponse,
    summary="Create a thread and stream its first message",
    responses={409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def create_thread_message_stream(
    request: ThreadMessageRequest,
    manager: RunManager,
) -> StreamingResponse:
    """Create a durable thread and stream its first turn as application events."""
    return await _stream_response(request, manager, thread_id=None)


@router.post(
    "/{thread_id}/messages",
    response_model=ThreadMessageResponse,
    response_model_exclude_none=True,
    summary="Continue a durable research thread",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def continue_thread(
    thread_id: UUID,
    request: ThreadMessageRequest,
    response: Response,
    service: ThreadService,
) -> ThreadMessageResponse:
    """Run one new message from the committed thread checkpoint."""
    result = await service.research(
        request.messages[0].content,
        thread_id=thread_id,
        request_key=request.request_key,
    )
    response.headers["Content-Location"] = (
        f"/api/v1/threads/{result.thread_id}/messages"
    )
    return _message_response(result)


@router.post(
    "/{thread_id}/messages/stream",
    response_class=StreamingResponse,
    summary="Continue a durable thread with streaming events",
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def continue_thread_stream(
    thread_id: UUID,
    request: ThreadMessageRequest,
    manager: RunManager,
) -> StreamingResponse:
    """Stream one new turn from the thread's committed checkpoint."""
    return await _stream_response(request, manager, thread_id=thread_id)


@router.get(
    "",
    response_model=ThreadListResponse,
    summary="List durable research threads",
)
async def list_threads(
    service: ThreadService,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ThreadListResponse:
    """Return threads ordered by most recent activity."""
    page = await service.list_threads(limit=limit, offset=offset)
    return ThreadListResponse(
        threads=[_thread_response(thread) for thread in page.threads],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/{thread_id}",
    response_model=ThreadResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Get durable thread metadata",
)
async def get_thread(
    thread_id: UUID,
    service: ThreadService,
) -> ThreadResponse:
    """Return one durable thread."""
    return _thread_response(await service.get_thread(thread_id))


@router.get(
    "/{thread_id}/messages",
    response_model=ThreadTranscriptResponse,
    response_model_exclude_none=True,
    responses={404: {"model": ErrorResponse}},
    summary="Get a durable thread transcript",
)
async def get_thread_messages(
    thread_id: UUID,
    service: ThreadService,
) -> ThreadTranscriptResponse:
    """Return all durable turns in conversation order."""
    turns = await service.list_turns(thread_id)
    return ThreadTranscriptResponse(
        thread_id=thread_id,
        turns=[_turn_response(turn) for turn in turns],
    )
