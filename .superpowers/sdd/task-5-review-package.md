# Task 5 Review Package

## app/api/v1/chat.py
```
"""Streaming chat API."""

import json
import asyncio
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.governance.tool_invocations import PostgresToolInvocationRepository, ToolInvocationRecord
from app.models.base import async_session_maker, get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.events import SSEEvent
from app.schemas.message import MessageCreate
from app.schemas.tools import ToolCallPayload
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
        .order_by(Message.created_at.desc())
        .limit(_RECENT_CONTEXT_LIMIT)
    )
    messages = list(reversed(result.scalars().all()))
    return [{"role": message.role, "content": message.content} for message in messages]


async def generate_sse_stream(
    conversation_id: str,
    user_message: str,
    user_id: str,
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
            await PostgresToolInvocationRepository().create(
                ToolInvocationRecord(
                    call_id=call_id,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    tool=payload.tool,
                    arguments=payload.arguments,
                )
            )
            async with async_session_maker() as db:
                await save_message(
                    db,
                    conversation_id,
                    "assistant",
                    "正在收集旅行需求。",
                    {"action": decision.action, "tool_call": payload.model_dump()},
                )
            yield sse(event("tool_call", payload.model_dump()))
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

    return StreamingResponse(
        generate_sse_stream(conversation_id, data.content, str(user.id)),
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
```

## app/api/v1/__init__.py
```
```

## tests/test_chat_main_agent_flow.py
```
import inspect
import json
import uuid
from contextlib import asynccontextmanager

import pytest

from app.api.v1 import chat
from app.governance.tool_invocations import InMemoryToolInvocationRepository
from app.schemas.tools import MainAgentDecision


class RecordingSession:
    def __init__(self, history):
        self.history = [HistoryMessage(**item) for item in history]
        self.saved_messages = []

    def add(self, message):
        self.saved_messages.append(message)

    async def commit(self):
        pass

    async def refresh(self, message):
        if message.id is None:
            message.id = uuid.uuid4()

    async def execute(self, _statement):
        return MessageResult(self.history)


class HistoryMessage:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class MessageResult:
    def __init__(self, messages):
        self.messages = messages

    def scalars(self):
        return self

    def all(self):
        return self.messages


class DecisionAgent:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    async def decide(self, message, context):
        self.calls.append((message, context))
        return self.decision


def session_factory(session):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


async def stream_events():
    return [
        json.loads(frame.removeprefix("data: ").strip())
        async for frame in chat.generate_sse_stream(
            str(uuid.uuid4()), "message", str(uuid.uuid4())
        )
    ]


def configure_stream(monkeypatch, decision, history=None):
    session = RecordingSession(history or [])
    agent = DecisionAgent(decision)
    repository = InMemoryToolInvocationRepository()
    monkeypatch.setattr(chat, "async_session_maker", session_factory(session))
    monkeypatch.setattr(chat, "MainAgentService", lambda: agent)
    monkeypatch.setattr(chat, "PostgresToolInvocationRepository", lambda: repository)
    return session, agent, repository


@pytest.mark.asyncio
async def test_affirmation_persists_one_form_call_and_emits_tool_call(monkeypatch):
    session, agent, repository = configure_stream(
        monkeypatch,
        MainAgentDecision(
            action="collect_trip_requirements",
            reason="affirmed proactive offer",
            initial_values={},
        ),
        history=[{"role": "assistant", "content": "Need help planning a trip?"}],
    )

    events = await stream_events()

    assert [event["type"] for event in events] == ["tool_call", "done"]
    assert len(repository.records) == 1
    record = next(iter(repository.records.values()))
    assert events[0]["call_id"] == record.call_id
    assert events[0]["tool"] == "collect_trip_requirements"
    assert events[0]["arguments"] == {"initial_values": {}}
    assert record.arguments == {"initial_values": {}}
    assert uuid.UUID(record.user_id)
    assert uuid.UUID(record.conversation_id)
    assert agent.calls == [("message", [{"role": "assistant", "content": "Need help planning a trip?"}])]
    assert [message.role for message in session.saved_messages] == ["user", "assistant"]
    assert session.saved_messages[1].extra_info["action"] == "collect_trip_requirements"
    assert session.saved_messages[1].extra_info["tool_call"]["call_id"] == record.call_id


@pytest.mark.asyncio
async def test_direct_planning_prepopulates_destination_without_supervisor(monkeypatch):
    _, _, repository = configure_stream(
        monkeypatch,
        MainAgentDecision(
            action="collect_trip_requirements",
            reason="explicit planning request",
            initial_values={"destination": "Chengdu"},
        ),
    )

    events = await stream_events()

    assert events[0]["type"] == "tool_call"
    assert events[0]["arguments"] == {"initial_values": {"destination": "Chengdu"}}
    record = next(iter(repository.records.values()))
    assert record.arguments == events[0]["arguments"]
    source = inspect.getsource(chat.generate_sse_stream)
    assert "TripCoordinator" not in source
    assert "PostgresDraftRepository" not in source
    assert "TripDraftRecord" not in source
    assert "hard_missing" not in source
    assert "classify_intent" not in source


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["answer_open_question", "recommend_destination"])
async def test_rag_actions_call_only_rag_and_persist_their_action(monkeypatch, action):
    session, _, _ = configure_stream(
        monkeypatch,
        MainAgentDecision(action=action, reason="RAG response"),
    )
    calls = []

    async def answer(question):
        calls.append(question)
        return "RAG answer"

    monkeypatch.setattr(chat, "answer_open_question", answer)
    monkeypatch.setattr(
        chat,
        "PostgresToolInvocationRepository",
        lambda: (_ for _ in ()).throw(AssertionError("form repository must not be used")),
    )

    events = await stream_events()

    assert [event["type"] for event in events] == ["token", "done"]
    assert events[0]["content"] == "RAG answer"
    assert events[0]["payload"]["action"] == action
    assert calls == ["message"]
    assert session.saved_messages[-1].extra_info["action"] == action


@pytest.mark.asyncio
async def test_direct_response_calls_neither_rag_nor_supervisor(monkeypatch):
    session, _, _ = configure_stream(
        monkeypatch,
        MainAgentDecision(
            action="direct_response",
            reason="conversational",
            response="Hello there",
        ),
    )

    async def unexpected_rag(_question):
        raise AssertionError("RAG must not be used")

    monkeypatch.setattr(chat, "answer_open_question", unexpected_rag)
    monkeypatch.setattr(
        chat,
        "PostgresToolInvocationRepository",
        lambda: (_ for _ in ()).throw(AssertionError("form repository must not be used")),
    )

    events = await stream_events()

    assert [event["type"] for event in events] == ["token", "done"]
    assert events[0]["content"] == "Hello there"
    assert events[0]["payload"]["action"] == "direct_response"
    assert session.saved_messages[-1].extra_info["action"] == "direct_response"
```

# Fix Addendum

## Current app/api/v1/chat.py
```
"""Streaming chat API."""

import json
import asyncio
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.governance.tool_invocations import PostgresToolInvocationRepository, ToolInvocationRecord
from app.models.base import async_session_maker, get_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.events import SSEEvent
from app.schemas.message import MessageCreate
from app.schemas.tools import ToolCallPayload
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

    return StreamingResponse(
        generate_sse_stream(conversation_id, data.content, str(user.id)),
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
```

## Current app/governance/tool_invocations.py
```
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import async_session_maker
from app.models.conversation import Conversation
from app.models.tool_invocation import ToolInvocation


class ToolInvocationRecord(BaseModel):
    call_id: str
    user_id: str
    conversation_id: str
    tool: str
    status: str = "pending"
    arguments: dict[str, Any] = Field(default_factory=dict)
    partial_values: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CompletionOutcome(BaseModel):
    record: ToolInvocationRecord
    completed_now: bool


class ToolInvocationRepository(Protocol):
    async def create(self, record: ToolInvocationRecord) -> ToolInvocationRecord: ...
    async def get_for_user(self, call_id: str, user_id: str) -> ToolInvocationRecord | None: ...
    async def update_partial(
        self, call_id: str, user_id: str, partial_values: dict[str, Any]
    ) -> ToolInvocationRecord | None: ...
    async def complete_once(
        self, call_id: str, user_id: str, result: dict[str, Any] | None
    ) -> CompletionOutcome | None: ...


class InMemoryToolInvocationRepository:
    def __init__(self):
        self.records: dict[str, ToolInvocationRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: ToolInvocationRecord) -> ToolInvocationRecord:
        async with self._lock:
            if record.call_id in self.records:
                raise ValueError(f"Tool invocation already exists: {record.call_id}")
            stored = record.model_copy(deep=True)
            self.records[record.call_id] = stored
            return stored.model_copy(deep=True)

    async def get_for_user(self, call_id: str, user_id: str) -> ToolInvocationRecord | None:
        async with self._lock:
            record = self.records.get(call_id)
            if record is None or record.user_id != user_id:
                return None
            return record.model_copy(deep=True)

    async def update_partial(
        self, call_id: str, user_id: str, partial_values: dict[str, Any]
    ) -> ToolInvocationRecord | None:
        async with self._lock:
            record = self.records.get(call_id)
            if record is None or record.user_id != user_id:
                return None
            record.partial_values = {**record.partial_values, **deepcopy(partial_values)}
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def complete_once(
        self, call_id: str, user_id: str, result: dict[str, Any] | None
    ) -> CompletionOutcome | None:
        async with self._lock:
            record = self.records.get(call_id)
            if record is None or record.user_id != user_id:
                return None
            if record.status == "completed":
                return CompletionOutcome(record=record.model_copy(deep=True), completed_now=False)
            record.status = "completed"
            record.result = deepcopy(result)
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return CompletionOutcome(record=record.model_copy(deep=True), completed_now=True)


class PostgresToolInvocationRepository:
    def __init__(self, session_factory=async_session_maker):
        self.session_factory = session_factory

    async def create(self, record: ToolInvocationRecord) -> ToolInvocationRecord:
        async with self.session_factory() as session:
            async with session.begin():
                await self.create_in_session(session, record)
        return record.model_copy(deep=True)

    async def create_in_session(
        self,
        session: AsyncSession,
        record: ToolInvocationRecord,
    ) -> ToolInvocationRecord:
        user_id = UUID(record.user_id)
        conversation_id = UUID(record.conversation_id)
        owned_conversation = await session.scalar(
            select(Conversation.id).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        if owned_conversation is None:
            raise PermissionError("Conversation does not belong to the user")
        session.add(
            ToolInvocation(
                call_id=record.call_id,
                user_id=user_id,
                conversation_id=conversation_id,
                tool=record.tool,
                status=record.status,
                arguments=deepcopy(record.arguments),
                partial_values=deepcopy(record.partial_values),
                result=deepcopy(record.result),
                version=record.version,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
        )
        return record.model_copy(deep=True)

    async def get_for_user(self, call_id: str, user_id: str) -> ToolInvocationRecord | None:
        async with self.session_factory() as session:
            entity = await session.scalar(
                select(ToolInvocation).where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                )
            )
            return self._record_from_entity(entity) if entity else None

    async def update_partial(
        self, call_id: str, user_id: str, partial_values: dict[str, Any]
    ) -> ToolInvocationRecord | None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session, session.begin():
            entity = await session.scalar(
                select(ToolInvocation)
                .where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                )
                .with_for_update()
            )
            if entity is None:
                return None
            entity.partial_values = {**entity.partial_values, **deepcopy(partial_values)}
            entity.version += 1
            entity.updated_at = now
            await session.flush()
            return self._record_from_entity(entity)

    async def complete_once(
        self, call_id: str, user_id: str, result: dict[str, Any] | None
    ) -> CompletionOutcome | None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session, session.begin():
            completion = await session.execute(
                update(ToolInvocation)
                .where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                    ToolInvocation.status != "completed",
                )
                .values(
                    status="completed",
                    result=deepcopy(result),
                    version=ToolInvocation.version + 1,
                    updated_at=now,
                )
                .returning(ToolInvocation)
            )
            entity = completion.scalar_one_or_none()
            if entity is None:
                entity = await session.scalar(
                    select(ToolInvocation).where(
                        ToolInvocation.call_id == call_id,
                        ToolInvocation.user_id == UUID(user_id),
                    )
                )
                return (
                    CompletionOutcome(record=self._record_from_entity(entity), completed_now=False)
                    if entity
                    else None
                )
            return CompletionOutcome(record=self._record_from_entity(entity), completed_now=True)

    @staticmethod
    def _record_from_entity(entity: ToolInvocation) -> ToolInvocationRecord:
        return ToolInvocationRecord(
            call_id=entity.call_id,
            user_id=str(entity.user_id),
            conversation_id=str(entity.conversation_id),
            tool=entity.tool,
            status=entity.status,
            arguments=deepcopy(entity.arguments),
            partial_values=deepcopy(entity.partial_values),
            result=deepcopy(entity.result),
            version=entity.version,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
```

## Current tests/test_chat_main_agent_flow.py
```
import inspect
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects import postgresql

from app.api.v1 import chat
from app.schemas.tools import MainAgentDecision


class RecordingSession:
    def __init__(self, history, *, fail_assistant_add=False):
        self.history = [HistoryMessage(**item) for item in history]
        self.saved_messages = []
        self.persisted_invocations = []
        self._staged_messages = []
        self._staged_invocations = []
        self._in_transaction = False
        self.fail_assistant_add = fail_assistant_add

    def add(self, entity):
        if self._in_transaction:
            if getattr(entity, "role", None) == "assistant" and self.fail_assistant_add:
                raise RuntimeError("assistant metadata write failed")
            self._staged_messages.append(entity)
            return
        self.saved_messages.append(entity)

    async def commit(self):
        pass

    async def refresh(self, message):
        if message.id is None:
            message.id = uuid.uuid4()

    async def execute(self, _statement):
        return MessageResult(self.history)

    def begin(self):
        return RecordingTransaction(self)

    def stage_invocation(self, record):
        self._staged_invocations.append(record)


class RecordingTransaction:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        self.session._in_transaction = True
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        self.session._in_transaction = False
        if exc_type is None:
            self.session.saved_messages.extend(self.session._staged_messages)
            self.session.persisted_invocations.extend(self.session._staged_invocations)
        self.session._staged_messages.clear()
        self.session._staged_invocations.clear()
        return False


class HistoryMessage:
    def __init__(self, role, content, message_id=None, created_at=None):
        self.role = role
        self.content = content
        self.id = message_id or uuid.uuid4()
        self.created_at = created_at or datetime.now(timezone.utc)


class MessageResult:
    def __init__(self, messages):
        self.messages = messages

    def scalars(self):
        return self

    def all(self):
        return self.messages


class DecisionAgent:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    async def decide(self, message, context):
        self.calls.append((message, context))
        return self.decision


class SessionAwareRecordingRepository:
    async def create_in_session(self, session, record):
        session.stage_invocation(record)
        return record


def session_factory(session):
    @asynccontextmanager
    async def factory():
        yield session

    return factory


async def stream_events():
    return [
        json.loads(frame.removeprefix("data: ").strip())
        async for frame in chat.generate_sse_stream(
            str(uuid.uuid4()), "message", str(uuid.uuid4())
        )
    ]


def configure_stream(monkeypatch, decision, history=None, *, fail_assistant_add=False):
    session = RecordingSession(history or [], fail_assistant_add=fail_assistant_add)
    agent = DecisionAgent(decision)
    repository = SessionAwareRecordingRepository()
    monkeypatch.setattr(chat, "async_session_maker", session_factory(session))
    monkeypatch.setattr(chat, "MainAgentService", lambda: agent)
    monkeypatch.setattr(chat, "PostgresToolInvocationRepository", lambda: repository)
    return session, agent, repository


@pytest.fixture
def supervisor_calls(monkeypatch):
    from app.agents import supervisor

    calls = []

    async def unexpected_supervisor(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Supervisor must not be called")

    monkeypatch.setattr(supervisor, "run_travel_planning", unexpected_supervisor)
    return calls


@pytest.mark.asyncio
async def test_affirmation_persists_one_form_call_and_emits_tool_call(monkeypatch, supervisor_calls):
    session, agent, _ = configure_stream(
        monkeypatch,
        MainAgentDecision(
            action="collect_trip_requirements",
            reason="affirmed proactive offer",
            initial_values={},
        ),
        history=[{"role": "assistant", "content": "Need help planning a trip?"}],
    )

    events = await stream_events()

    assert [event["type"] for event in events] == ["tool_call", "done"]
    assert len(session.persisted_invocations) == 1
    record = session.persisted_invocations[0]
    assert events[0]["call_id"] == record.call_id
    assert events[0]["tool"] == "collect_trip_requirements"
    assert events[0]["arguments"] == {"initial_values": {}}
    assert record.arguments == {"initial_values": {}}
    assert uuid.UUID(record.user_id)
    assert uuid.UUID(record.conversation_id)
    assert agent.calls == [("message", [{"role": "assistant", "content": "Need help planning a trip?"}])]
    assert [message.role for message in session.saved_messages] == ["user", "assistant"]
    assert session.saved_messages[1].extra_info["action"] == "collect_trip_requirements"
    assert session.saved_messages[1].extra_info["tool_call"]["call_id"] == record.call_id
    assert supervisor_calls == []


@pytest.mark.asyncio
async def test_direct_planning_prepopulates_destination_without_supervisor(monkeypatch, supervisor_calls):
    session, _, _ = configure_stream(
        monkeypatch,
        MainAgentDecision(
            action="collect_trip_requirements",
            reason="explicit planning request",
            initial_values={"destination": "Chengdu"},
        ),
    )

    events = await stream_events()

    assert events[0]["type"] == "tool_call"
    assert events[0]["arguments"] == {"initial_values": {"destination": "Chengdu"}}
    record = session.persisted_invocations[0]
    assert record.arguments == events[0]["arguments"]
    source = inspect.getsource(chat.generate_sse_stream)
    assert "TripCoordinator" not in source
    assert "PostgresDraftRepository" not in source
    assert "TripDraftRecord" not in source
    assert "hard_missing" not in source
    assert "classify_intent" not in source
    assert supervisor_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["answer_open_question", "recommend_destination"])
async def test_rag_actions_call_only_rag_and_persist_their_action(monkeypatch, action, supervisor_calls):
    session, _, _ = configure_stream(
        monkeypatch,
        MainAgentDecision(action=action, reason="RAG response"),
    )
    calls = []

    async def answer(question):
        calls.append(question)
        return "RAG answer"

    monkeypatch.setattr(chat, "answer_open_question", answer)
    monkeypatch.setattr(
        chat,
        "PostgresToolInvocationRepository",
        lambda: (_ for _ in ()).throw(AssertionError("form repository must not be used")),
    )

    events = await stream_events()

    assert [event["type"] for event in events] == ["token", "done"]
    assert events[0]["content"] == "RAG answer"
    assert events[0]["payload"]["action"] == action
    assert calls == ["message"]
    assert session.saved_messages[-1].extra_info["action"] == action
    assert supervisor_calls == []


@pytest.mark.asyncio
async def test_direct_response_calls_neither_rag_nor_supervisor(monkeypatch, supervisor_calls):
    session, _, _ = configure_stream(
        monkeypatch,
        MainAgentDecision(
            action="direct_response",
            reason="conversational",
            response="Hello there",
        ),
    )

    async def unexpected_rag(_question):
        raise AssertionError("RAG must not be used")

    monkeypatch.setattr(chat, "answer_open_question", unexpected_rag)
    monkeypatch.setattr(
        chat,
        "PostgresToolInvocationRepository",
        lambda: (_ for _ in ()).throw(AssertionError("form repository must not be used")),
    )

    events = await stream_events()

    assert [event["type"] for event in events] == ["token", "done"]
    assert events[0]["content"] == "Hello there"
    assert events[0]["payload"]["action"] == "direct_response"
    assert session.saved_messages[-1].extra_info["action"] == "direct_response"
    assert supervisor_calls == []


@pytest.mark.asyncio
async def test_collect_form_rolls_back_invocation_and_assistant_metadata_on_failure(monkeypatch, supervisor_calls):
    session, _, _ = configure_stream(
        monkeypatch,
        MainAgentDecision(
            action="collect_trip_requirements",
            reason="explicit planning request",
            initial_values={"destination": "Chengdu"},
        ),
        fail_assistant_add=True,
    )

    events = await stream_events()

    assert [event["type"] for event in events] == ["error", "done"]
    assert session.persisted_invocations == []
    assert [message.role for message in session.saved_messages] == ["user"]
    assert supervisor_calls == []


class ContextStatementSession:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return MessageResult(self.rows)


@pytest.mark.asyncio
async def test_recent_context_excludes_current_message_limits_and_orders_ties_deterministically():
    current_message_id = uuid.uuid4()
    same_timestamp = datetime(2026, 7, 22, tzinfo=timezone.utc)
    newest_first = [
        HistoryMessage("assistant", f"message-{index}", uuid.UUID(int=index), same_timestamp)
        for index in range(13, 1, -1)
    ]
    session = ContextStatementSession(newest_first)

    context = await chat._load_recent_context(session, str(uuid.uuid4()), current_message_id)

    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "message.id !=" in sql
    assert "ORDER BY message.created_at DESC, message.id DESC" in sql
    assert "LIMIT" in sql
    assert len(context) == 12
    assert [item["content"] for item in context] == [f"message-{index}" for index in range(2, 14)]
    assert all(item["content"] != "current message" for item in context)
```
