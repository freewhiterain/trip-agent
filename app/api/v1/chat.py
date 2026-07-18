"""
流式对话 API（SSE）
"""
import json
import asyncio
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
