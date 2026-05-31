"""
会话相关的 Pydantic 模型
"""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime
import uuid


class ConversationCreate(BaseModel):
    """创建会话"""
    title: Optional[str] = "新对话"


class ConversationUpdate(BaseModel):
    """更新会话"""
    title: Optional[str] = None
    status: Optional[str] = None


class ConversationResponse(BaseModel):
    """会话响应"""
    id: uuid.UUID
    user_id: uuid.UUID
    title: str
    status: str
    extra_info: dict
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
