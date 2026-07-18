"""
流式对话 API（SSE）
"""
import json
import asyncio
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from langchain_core.messages import HumanMessage, AIMessage, AIMessageChunk
from app.models.base import get_db, async_session_maker
from app.models.user import User
from app.models.conversation import Conversation
from app.models.message import Message
from app.schemas.message import MessageCreate
from app.api.dependencies import get_current_user
from app.agents.factory import create_chat_agent
from app.agents.supervisor import run_travel_planning
from app.config import settings
from app.core.checkpointer import get_checkpointer
from app.governance.events import PublishingEventRepository, TaskEventService
from app.governance.postgres import PostgresEventRepository
from app.schemas.events import SSEEvent
from app.services.planning import RequirementExtractor, render_plan_markdown
from app.utils.logger import app_logger

router = APIRouter(prefix="/chat", tags=["对话"])


async def save_message(
        db: AsyncSession,
        conversation_id: str,
        role: str,
        content: str,
        extra_info: dict = None
) -> Message:
    """保存消息到数据库"""

    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        extra_info=extra_info or {}
    )

    db.add(message)
    await db.commit()
    await db.refresh(message)

    return message


def sse(data: dict) -> str:
    """SSE 标准 data 帧"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


EVENT_TYPE_MAP = {
    "task_created": "task",
    "plan_created": "plan",
    "worker_started": "worker",
    "worker_completed": "worker",
    "evidence_collected": "evidence",
    "approval_requested": "approval",
    "approval_received": "approval",
    "plan_generated": "result",
    "task_completed": "task",
    "task_failed": "error",
}


def _public_error(exc: Exception) -> dict:
    if isinstance(exc, ValueError):
        return {"code": "validation_error", "message": str(exc), "retryable": False}
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return {"code": "timeout", "message": "外部服务响应超时，请稍后重试。", "retryable": True}
    return {"code": "internal_error", "message": "旅行规划暂时无法完成，请稍后重试。", "retryable": True}


async def generate_supervisor_sse_stream(
    conversation_id: str,
    user_message: str,
    user_id: str,
):
    task_id = uuid4().hex
    sequence = 0

    def event(event_type: str, payload: dict | None = None):
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
            history_result = await db.execute(
                select(Message.content)
                .where(Message.conversation_id == conversation_id, Message.role == "user")
                .order_by(Message.created_at.desc())
                .limit(6)
            )
            recent_user_messages = list(reversed(history_result.scalars().all()))
        requirement_context = "\n".join(recent_user_messages) or user_message
        draft_requirement = await RequirementExtractor().extract(requirement_context)
        missing = draft_requirement.missing_fields()
        if missing:
            content = f"为了开始并行规划，还需要你提供：{'、'.join(missing)}。"
            yield sse(event("token", {"content": content}))
            async with async_session_maker() as db:
                await save_message(db, conversation_id, "assistant", content)
            yield sse(event("done"))
            return

        requirement = draft_requirement.to_requirement()
        queue: asyncio.Queue = asyncio.Queue()
        publishing_repository = PublishingEventRepository(
            PostgresEventRepository(),
            queue.put,
        )
        planning_task = asyncio.create_task(
            run_travel_planning(
                requirement,
                checkpointer=await get_checkpointer(),
                event_service=TaskEventService(publishing_repository),
                task_id=task_id,
                user_id=user_id,
                conversation_id=conversation_id,
            )
        )

        while not planning_task.done() or not queue.empty():
            try:
                stored_event = await asyncio.wait_for(queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            sequence = max(sequence, stored_event.sequence)
            mapped_type = EVENT_TYPE_MAP.get(stored_event.event_type, "task")
            yield sse(
                SSEEvent(
                    type=mapped_type,
                    task_id=task_id,
                    conversation_id=conversation_id,
                    sequence=stored_event.sequence,
                    payload={"event": stored_event.event_type, **stored_event.payload},
                ).legacy_payload()
            )

        draft = await planning_task
        markdown = render_plan_markdown(draft)
        yield sse(event("result", {"draft": draft.model_dump(mode="json")}))
        yield sse(event("token", {"content": markdown}))
        async with async_session_maker() as db:
            await save_message(
                db,
                conversation_id,
                "assistant",
                markdown,
                {"task_id": task_id, "draft": draft.model_dump(mode="json")},
            )
        yield sse(event("done"))
    except Exception as exc:
        app_logger.exception("Supervisor SSE 旅行规划错误")
        yield sse(event("error", _public_error(exc)))
        yield sse(event("done"))


async def generate_sse_stream(
        conversation_id: str,
        user_message: str,
        user_id: str,
):
    """生成 SSE 流（使用独立的 db session 以避免生命周期问题）"""
    assistant_message = ""

    try:
        # 用独立 session 保存用户消息
        async with async_session_maker() as db:
            await save_message(db, conversation_id, "user", user_message)

        if settings.travel_agent_mode.strip().lower() == "supervisor":
            async for frame in generate_supervisor_sse_stream(
                conversation_id,
                user_message,
                user_id,
            ):
                yield frame
            return

        # 创建 agent
        agent = await create_chat_agent()

        # 输入必须是字典格式（LangGraph StateGraph 期望 state 的部分更新）
        input_data = {
            "messages": [HumanMessage(content=user_message)],
            "user_id": user_id,
        }

        # 使用 astream_events 获取更细粒度的流式输出
        async for event in agent.astream_events(
                input_data,
                config={
                    "configurable": {
                        "thread_id": conversation_id
                    }
                },
                version="v2"
        ):
            kind = event.get("event")

            # 捕获 LLM 流式输出
            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    token = chunk.content
                    assistant_message += token
                    yield sse({
                        "type": "token",
                        "content": token,
                    })

            # 捕获工具调用信息
            elif kind == "on_tool_start":
                tool_name = event.get("name", "")
                yield sse({
                    "type": "tool_call",
                    "tool": tool_name,
                })

            await asyncio.sleep(0)

        # 保存 AI 回复
        if assistant_message.strip():
            async with async_session_maker() as db:
                await save_message(
                    db,
                    conversation_id,
                    "assistant",
                    assistant_message,
                )

        yield sse({"type": "done"})

    except Exception as e:
        app_logger.exception("SSE 流式对话错误")
        yield sse({
            "type": "error",
            "message": str(e),
        })


@router.post("/stream/{conversation_id}")
async def stream_chat(
        conversation_id: str,
        data: MessageCreate,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """
    流式对话（SSE）

    Returns:
        StreamingResponse: SSE 流式响应
    """

    # 验证会话归属
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.id)
    )

    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在"
        )

    # 返回 SSE 流（用独立 session 在生成器内部管理，避免依赖注入的 session 被关闭）
    return StreamingResponse(
        generate_sse_stream(conversation_id, data.content, str(user.id)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # 禁用 Nginx 缓冲
        }
    )


@router.get("/history/{conversation_id}")
async def get_chat_history(
        conversation_id: str,
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
):
    """获取会话历史消息"""

    # 验证会话归属
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .where(Conversation.user_id == user.id)
    )

    conversation = result.scalar_one_or_none()

    if not conversation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="会话不存在"
        )

    # 查询消息
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at)
    )

    messages = result.scalars().all()

    return {
        "conversation": conversation.to_dict(),
        "messages": [m.to_dict() for m in messages]
    }
