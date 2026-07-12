"""Chat endpoint — a thin POST+SSE bridge delegating to ChatUseCase.

Confirmation and rate-limiting run synchronously before the stream opens (their
errors become the HTTP error envelope); everything after is streamed as SSE.
Route handler stays within the 5-10 line budget via small helpers.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import json
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from src.application.chat.events import TextDelta
from src.application.chat.pending_actions import pending_action_store
from src.application.chat.system_prompt import build_system_prompt
from src.application.chat.use_case import ChatCommand, ChatUseCase
from src.application.runner import execute_use_case
from src.application.tools.registry import (
    build_tools,
    execute_confirmed_action,
    execute_tool,
)
from src.application.use_cases.get_dashboard_stats import (
    DashboardStatsResult,
    GetDashboardStatsCommand,
    GetDashboardStatsUseCase,
)
from src.application.use_cases.record_chat_feedback import (
    RecordChatFeedbackCommand,
    RecordChatFeedbackUseCase,
)
from src.application.use_cases.workflow_crud import (
    GetWorkflowCommand,
    GetWorkflowUseCase,
)
from src.config import get_logger
from src.config.settings import settings
from src.domain.entities.workflow import Workflow
from src.domain.exceptions import NotFoundError
from src.interface.api.chat_sse import QueueItem, stream_chat_response
from src.interface.api.deps import get_current_user_id, get_llm_client
from src.interface.api.rate_limit import InMemoryRateLimiter
from src.interface.api.schemas.chat import (
    ChatFeedbackRequest,
    ChatFeedbackResponse,
    ChatRequest,
)

logger = get_logger(__name__)

router = APIRouter(tags=["chat"])

_chat_limiter = InMemoryRateLimiter(
    max_requests=settings.chat.rate_limit_requests,
    window_seconds=settings.chat.rate_limit_window_seconds,
)


async def _handle_confirmation(body: ChatRequest, user_id: str) -> str | None:
    """Process a confirmation, if present, before the model turn.

    Returns a context string to append to the conversation so the model can
    acknowledge the outcome, or None when there is no confirmation.
    """
    if body.confirmation is None:
        return None
    action_id = UUID(body.confirmation.action_id)
    if body.confirmation.approved:
        action = pending_action_store.claim(action_id, user_id)
        result = await execute_confirmed_action(action, user_id)
        logger.info(
            "chat_action_confirmed", action_id=str(action_id), tool=action.tool_name
        )
        return (
            f"[The user confirmed the proposed action. "
            f"Result: {json.dumps(result)}. Acknowledge the change briefly.]"
        )
    pending_action_store.cancel(action_id, user_id)
    logger.info("chat_action_cancelled", action_id=str(action_id))
    return (
        "[The user cancelled the proposed action. "
        "Acknowledge the cancellation briefly.]"
    )


async def _fetch_library_stats(user_id: str) -> DashboardStatsResult | None:
    """Fetch per-user library stats for the system prompt; never kill chat.

    Stats are prompt garnish — any failure degrades to a "stats unavailable"
    line rather than a 500 before the stream even opens.
    """
    command = GetDashboardStatsCommand(user_id=user_id)
    try:
        return await execute_use_case(
            lambda uow: GetDashboardStatsUseCase().execute(command, uow),
            user_id=user_id,
        )
    except Exception:
        logger.warning("chat_library_stats_unavailable", exc_info=True)
        return None


async def _fetch_current_workflow(
    workflow_id: UUID | None, user_id: str
) -> Workflow | None:
    """Resolve the workflow the frontend reports as open in the editor.

    A stale or foreign id degrades to no context — the panel may race a
    deletion, and that must never 500 the chat.
    """
    if workflow_id is None:
        return None
    command = GetWorkflowCommand(user_id=user_id, workflow_id=workflow_id)
    try:
        result = await execute_use_case(
            lambda uow: GetWorkflowUseCase().execute(command, uow),
            user_id=user_id,
        )
    except NotFoundError:
        logger.warning("chat_current_workflow_not_found", workflow_id=str(workflow_id))
        return None
    return result.workflow


async def _build_command(
    body: ChatRequest, user_id: str, confirmation_context: str | None
) -> ChatCommand:
    """Build the ChatCommand, injecting per-user context into the prompt."""
    today = body.client_date or datetime.now(UTC).date()
    # Independent per-request reads — resolve them concurrently so the prompt
    # context costs the slower of the two, not their sum, before streaming.
    async with asyncio.TaskGroup() as tg:
        stats_task = tg.create_task(_fetch_library_stats(user_id))
        workflow_task = tg.create_task(
            _fetch_current_workflow(body.current_workflow_id, user_id)
        )
    system = build_system_prompt(stats_task.result(), workflow_task.result(), today)
    messages: list[dict[str, object]] = [
        {"role": m.role, "content": m.content} for m in body.messages
    ]
    if confirmation_context is not None:
        messages.append({"role": "user", "content": confirmation_context})
    return ChatCommand(
        messages=messages,
        system=system,
        tools=build_tools(),
        model_id=settings.chat.model_id,
        max_turns=settings.chat.max_turns,
        max_tokens=settings.chat.max_tokens,
        effort=body.effort or settings.chat.effort,
        user_id=user_id,
    )


def _bridge(
    use_case: ChatUseCase, command: ChatCommand
) -> Callable[[asyncio.Queue[QueueItem]], Awaitable[None]]:
    """Wrap the event generator into a queue-based run function for SSE."""

    async def _run(queue: asyncio.Queue[QueueItem]) -> None:
        async for event in use_case.execute(command):
            if isinstance(event, TextDelta):
                queue.put_nowait(event.text)
            else:
                queue.put_nowait(event)

    return _run


@router.post("/chat/feedback", status_code=201)
async def post_chat_feedback(
    body: ChatFeedbackRequest,
    user_id: str = Depends(get_current_user_id),
) -> ChatFeedbackResponse:
    command = RecordChatFeedbackCommand(
        user_id=user_id,
        prompt=body.prompt,
        generated_workflow_def=body.generated_workflow_def,
        signal=body.signal,
        note=body.note,
    )
    result = await execute_use_case(
        lambda uow: RecordChatFeedbackUseCase().execute(command, uow),
        user_id=user_id,
    )
    return ChatFeedbackResponse(id=result.feedback_id)


@router.post("/chat")
async def post_chat(
    body: ChatRequest,
    user_id: str = Depends(get_current_user_id),
) -> StreamingResponse:
    # Resolved in-body (not a second Depends): a user with no key (and no
    # server fallback) raises ChatUnavailableError here -> 503 CHAT_UNAVAILABLE
    # before any streaming. The user's own key wins over the server fallback.
    llm = await get_llm_client(user_id)
    _chat_limiter.check(user_id)
    confirmation_context = await _handle_confirmation(body, user_id)
    command = await _build_command(body, user_id, confirmation_context)
    return stream_chat_response(_bridge(ChatUseCase(llm, execute_tool), command))
