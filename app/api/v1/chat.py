"""Streaming chat API."""

import json
import asyncio
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.agents.agent_tools import run_agent_tool
from app.governance.tool_invocations import PostgresToolInvocationRepository, ToolInvocationRecord
from app.models.base import async_session_maker, get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.events import SSEEvent
from app.schemas.message import MessageCreate
from app.schemas.tools import AgentToolResult, ToolCallPayload
from app.services.main_agent import MainAgentService
from app.services.open_qa import answer_open_question
from app.utils.logger import app_logger


router = APIRouter(prefix="/chat", tags=["对话"])

_RECENT_CONTEXT_LIMIT = 12
_DIRECT_RESPONSE = "我可以解答旅行问题，也可以在你准备好时开始规划行程。"


async def save_message(
    db: AsyncSession,
    conversation_id: str,
    role: str,
    content: str,
    extra_info: dict | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        extra_info=extra_info or {},
    )
    db.add(message)
    await db.commit()
    await db.refresh(message)
    return message


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _public_error(exc: Exception) -> dict:
    if isinstance(exc, ValueError):
        return {"code": "validation_error", "message": str(exc), "retryable": False}
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return {"code": "timeout", "message": "外部服务响应超时，请稍后重试。", "retryable": True}
    return {"code": "internal_error", "message": "旅行规划暂时无法完成，请稍后重试。", "retryable": True}


async def _load_recent_context(
    db: AsyncSession,
    conversation_id: str,
    excluded_message_id,
) -> list[dict[str, str]]:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .where(Message.id != excluded_message_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(_RECENT_CONTEXT_LIMIT)
    )
    messages = list(reversed(result.scalars().all()))
    return [{"role": message.role, "content": message.content} for message in messages]


async def generate_sse_stream(
    conversation_id: str,
    user_message: str,
    user_id: str,
    registry=None,
):
    """Save one user turn, decide once, and stream its explicit action."""
    task_id = uuid4().hex
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=task_id,
            conversation_id=conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    try:
        async with async_session_maker() as db:
            saved_user_message = await save_message(db, conversation_id, "user", user_message)
            context = await _load_recent_context(db, conversation_id, saved_user_message.id)

        decision = await MainAgentService().decide(user_message, context)

        if decision.action == "collect_trip_requirements":
            call_id = uuid4().hex
            payload = ToolCallPayload(
                call_id=call_id,
                tool="collect_trip_requirements",
                arguments={"initial_values": decision.initial_values},
            )
            async with async_session_maker() as db:
                async with db.begin():
                    await PostgresToolInvocationRepository().create_in_session(
                        db,
                        ToolInvocationRecord(
                            call_id=call_id,
                            user_id=user_id,
                            conversation_id=conversation_id,
                            tool=payload.tool,
                            arguments=payload.arguments,
                        ),
                    )
                    db.add(
                        Message(
                            conversation_id=conversation_id,
                            role="assistant",
                            content="正在收集旅行需求。",
                            extra_info={"action": decision.action, "tool_call": payload.model_dump()},
                        )
                    )
            yield sse(event("tool_call", payload.model_dump()))
            yield sse(event("done"))
            return

        if decision.action == "invoke_agent_tool" and decision.tool_call is not None:
            call_id = uuid4().hex
            tool_call = decision.tool_call
            call_payload = {
                "call_id": call_id,
                "tool": tool_call.name,
                "arguments": tool_call.arguments.model_dump(mode="json"),
            }
            yield sse(event("tool_call", call_payload))

            if registry is None:
                result: AgentToolResult = await run_agent_tool(tool_call)
            else:
                result = await run_agent_tool(tool_call, registry=registry)
            result_payload = {
                "call_id": call_id,
                "tool": result.tool_name,
                "status": result.status,
                "result": result.model_dump(mode="json"),
            }
            yield sse(event("tool_result", result_payload))

            async with async_session_maker() as db:
                await save_message(
                    db,
                    conversation_id,
                    "assistant",
                    result.answer,
                    {
                        "action": decision.action,
                        "tool_call": call_payload,
                        "tool_result": result.model_dump(mode="json"),
                    },
                )
            yield sse(event("token", {"content": result.answer, "action": decision.action}))
            yield sse(event("done"))
            return

        if decision.action in {"answer_open_question", "recommend_destination"}:
            answer = await answer_open_question(user_message)
            async with async_session_maker() as db:
                await save_message(
                    db,
                    conversation_id,
                    "assistant",
                    answer,
                    {"action": decision.action},
                )
            yield sse(event("token", {"content": answer, "action": decision.action}))
            yield sse(event("done"))
            return

        response = decision.response or _DIRECT_RESPONSE
        async with async_session_maker() as db:
            await save_message(
                db,
                conversation_id,
                "assistant",
                response,
                {"action": decision.action},
            )
        yield sse(event("token", {"content": response, "action": decision.action}))
        yield sse(event("done"))
    except Exception as exc:
        app_logger.exception("SSE chat routing error")
        yield sse(event("error", _public_error(exc)))
        yield sse(event("done"))


@router.post("/stream/{conversation_id}")
async def stream_chat(
    conversation_id: str,
    data: MessageCreate,
    request: Request = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")

    app_state = getattr(getattr(request, "app", None), "state", None)
    registry = getattr(app_state, "planning_registry", None)
    return StreamingResponse(
        generate_sse_stream(
            conversation_id,
            data.content,
            str(user.id),
            registry=registry,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history/{conversation_id}")
async def get_chat_history(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.id)
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")

    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )
    messages = result.scalars().all()
    return {
        "conversation": conversation.to_dict(),
        "messages": [message.to_dict() for message in messages],
    }
