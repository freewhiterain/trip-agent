# Task 6 Review Package

## app/api/v1/tools.py
```
"""Tool-result SSE API."""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.agents.supervisor import run_travel_planning
from app.api.dependencies import get_current_user
from app.core.checkpointer import get_checkpointer
from app.governance.events import TaskEventService
from app.governance.postgres import PostgresEventRepository
from app.governance.tool_invocations import PostgresToolInvocationRepository
from app.models.base import async_session_maker
from app.models.message import Message
from app.models.user import User
from app.schemas.events import SSEEvent
from app.schemas.planning import TravelRequirement
from app.schemas.tools import ToolResultRequest
from app.services.open_qa import answer_open_question


router = APIRouter(prefix="/chat/tools", tags=["chat tools"])


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def save_assistant_message(conversation_id: str, content: str, extra_info: dict) -> None:
    async with async_session_maker() as db:
        message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            extra_info=extra_info,
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)


def recommendation_query(partial_values: dict) -> str:
    values = json.dumps(partial_values, ensure_ascii=False, sort_keys=True)
    return f"Recommend a travel destination based on these confirmed preferences: {values}"


async def existing_completion_stream(call_id: str, conversation_id: str, result: dict | None):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    yield sse(event("result", {"task_id": call_id, "status": "completed", "result": result}))
    yield sse(event("token", {"content": "A travel-planning task has already been submitted."}))
    yield sse(event("done"))


async def tool_result_stream(call_id: str, data: ToolResultRequest, user_id: str, record):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=record.conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    try:
        repository = PostgresToolInvocationRepository()

        if data.status == "recommend_destination":
            updated = await repository.update_partial(call_id, user_id, data.partial_values)
            if updated is None:
                raise ValueError("Tool invocation is unavailable")

            answer = await answer_open_question(recommendation_query(updated.partial_values))
            tool_result = {
                "tool": data.tool,
                "status": "awaiting_destination",
                "partial_values": updated.partial_values,
            }
            await save_assistant_message(
                record.conversation_id,
                answer,
                {"tool_result": tool_result},
            )
            yield sse(event("token", {"content": answer}))
            yield sse(event("tool_result", tool_result))
            yield sse(event("done"))
            return

        if data.status != "completed" or data.result is None:
            raise ValueError("A completed tool result requires destination, departure_date, and days")

        confirmed_result = data.result.model_dump(mode="json")
        outcome = await repository.complete_once(call_id, user_id, confirmed_result)
        if outcome is None:
            raise ValueError("Tool invocation is unavailable")
        if not outcome.completed_now:
            async for frame in existing_completion_stream(
                call_id, outcome.record.conversation_id, outcome.record.result
            ):
                yield frame
            return

        requirement = TravelRequirement(**data.result.model_dump())
        event_service = TaskEventService(PostgresEventRepository())
        draft = await run_travel_planning(
            requirement,
            checkpointer=await get_checkpointer(),
            event_service=event_service,
            task_id=call_id,
            user_id=user_id,
            conversation_id=outcome.record.conversation_id,
        )
        assistant_result = draft.model_dump(mode="json")
        tool_result = {
            "tool": data.tool,
            "status": "completed",
            "result": confirmed_result,
            "task_id": call_id,
        }
        assistant_content = json.dumps(assistant_result, ensure_ascii=False)
        await save_assistant_message(
            outcome.record.conversation_id,
            assistant_content,
            {"tool_result": tool_result, "assistant_result": assistant_result},
        )
        yield sse(event("result", {"task_id": call_id, "status": "completed", "result": assistant_result}))
        yield sse(event("token", {"content": assistant_content}))
        yield sse(event("done"))
    except Exception as exc:
        yield sse(
            event(
                "error",
                {"code": "tool_result_error", "message": str(exc), "retryable": False},
            )
        )
        yield sse(event("done"))


@router.post("/{call_id}/result")
async def submit_tool_result(
    call_id: str,
    data: ToolResultRequest,
    user: User = Depends(get_current_user),
):
    user_id = str(user.id)
    repository = PostgresToolInvocationRepository()
    record = await repository.get_for_user(call_id, user_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool invocation not found")
    if record.tool != data.tool:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tool does not match invocation")
    if data.status == "completed" and data.result is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Completed results require destination, departure_date, and days",
        )
    if data.status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cancelled tool results are not supported",
        )
    if record.status == "completed" and data.status == "completed":
        return StreamingResponse(
            existing_completion_stream(call_id, record.conversation_id, record.result),
            media_type="text/event-stream",
        )
    if record.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tool invocation is not pending")

    return StreamingResponse(
        tool_result_stream(call_id, data, user_id, record),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

## app/main.py
```
"""
FastAPI 应用入口
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.utils.logger import app_logger
from app.api.v1 import conversations, chat, planning, tools, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    import asyncio

    settings.validate_security()

    loop = asyncio.get_running_loop()
    app_logger.info(f"FastAPI 使用的事件循环: {type(loop).__name__}")

    from app.core.checkpointer import checkpointer_lifespan
    from app.mcp_core.client import MCPClientManager
    from app.core.store import store_lifespan

    app_logger.info("启动应用...")

    async with checkpointer_lifespan():
        app_logger.info("Checkpointer 已就绪")

        async with store_lifespan():
            app_logger.info("Store 已就绪")

            # 初始化 MCP（如果配置了的话）
            mcp = await MCPClientManager.get_instance()
            app_logger.info("MCP 服务初始化完成")

            yield

            # 关闭 MCP
            try:
                if hasattr(mcp, "close"):
                    await mcp.close()
                app_logger.info("MCP 服务已关闭")
            except Exception as e:
                app_logger.warning(f"MCP 关闭异常: {e}")

    app_logger.info("应用已关闭")


app = FastAPI(
    title="LangGraph 旅行规划系统",
    description="企业级多 Agent 旅行规划服务",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
app.include_router(users.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(tools.router, prefix="/api/v1")
app.include_router(planning.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "status": "healthy",
        "service": "LangGraph Travel Planner",
        "version": "1.0.0",
        "docs": "/docs"
    }
```

## app/api/v1/__init__.py
```
from app.api.v1 import tools

__all__ = ["tools"]
```

## tests/test_trip_form_tool_flow.py
```
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.dependencies import get_current_user
from app.governance.tool_invocations import CompletionOutcome, ToolInvocationRecord
from app.main import app
from app.schemas.planning import TravelRequirement


class InMemoryInvocationRepository:
    def __init__(self, records):
        self.records = {record.call_id: record.model_copy(deep=True) for record in records}

    async def get_for_user(self, call_id, user_id):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        return record.model_copy(deep=True)

    async def update_partial(self, call_id, user_id, partial_values):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        record.partial_values = {**record.partial_values, **partial_values}
        record.version += 1
        return record.model_copy(deep=True)

    async def complete_once(self, call_id, user_id, result):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        if record.status == "completed":
            return CompletionOutcome(record=record.model_copy(deep=True), completed_now=False)
        record.status = "completed"
        record.result = result
        record.version += 1
        return CompletionOutcome(record=record.model_copy(deep=True), completed_now=True)


class RecordingSession:
    def __init__(self):
        self.messages = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def add(self, message):
        self.messages.append(message)

    async def commit(self):
        return None

    async def refresh(self, _message):
        return None


class RecordingEventRepository:
    def __init__(self):
        self.events = []

    async def append(self, event):
        event.sequence = len(self.events) + 1
        self.events.append(event)
        return event


class FakeDraft:
    def __init__(self, destination):
        self.destination = destination

    def model_dump(self, *, mode):
        assert mode == "json"
        return {"destination": self.destination, "summary": "confirmed itinerary"}


def invocation(*, user_id, status="pending", tool="collect_trip_requirements"):
    return ToolInvocationRecord(
        call_id="call-1",
        user_id=user_id,
        conversation_id=str(uuid4()),
        tool=tool,
        status=status,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def parse_sse(response):
    return [
        json.loads(frame.removeprefix("data: ").strip())
        for frame in response.text.split("\n\n")
        if frame.strip()
    ]


@asynccontextmanager
async def endpoint_client(user):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def configure_endpoint(monkeypatch, repository):
    from app.api.v1 import tools

    session = RecordingSession()
    events = RecordingEventRepository()
    monkeypatch.setattr(tools, "PostgresToolInvocationRepository", lambda: repository, raising=False)
    monkeypatch.setattr(tools, "PostgresEventRepository", lambda: events, raising=False)
    monkeypatch.setattr(tools, "async_session_maker", lambda: session, raising=False)

    async def checkpointer():
        return None

    monkeypatch.setattr(tools, "get_checkpointer", checkpointer, raising=False)
    return session, events


def test_trip_form_result_route_is_registered():
    paths = {route.path for route in app.routes}

    assert "/api/v1/chat/tools/{call_id}/result" in paths


@pytest.mark.asyncio
async def test_tool_result_rejects_another_users_call(monkeypatch):
    owner_id = str(uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=owner_id)])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(SimpleNamespace(id=uuid4())) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": {
                "destination": "Kyoto", "departure_date": "2026-08-03", "days": 4,
            }},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        None,
        {"destination": "Kyoto", "days": 4},
        {"destination": "Kyoto", "departure_date": "invalid", "days": 4},
        {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 0},
    ],
)
async def test_completed_result_requires_valid_trip_fields(monkeypatch, result):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": result},
        )

    assert response.status_code == 422
    assert repository.records["call-1"].status == "pending"


@pytest.mark.asyncio
async def test_tool_result_rejects_wrong_stored_tool(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository(
        [invocation(user_id=str(user.id), tool="other_tool")]
    )
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "recommend_destination"},
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_recommendation_requires_a_pending_call(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id), status="cancelled")])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "recommend_destination"},
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_recommendation_keeps_call_pending_and_returns_rag_answer(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    session, _ = configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    queries = []

    async def recommend(query):
        queries.append(query)
        return "Kyoto works well for a four-day food-focused trip."

    monkeypatch.setattr(tools, "answer_open_question", recommend, raising=False)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={
                "tool": "collect_trip_requirements",
                "status": "recommend_destination",
                "partial_values": {"days": 4, "interests": "food"},
            },
        )

    events = parse_sse(response)
    assert response.status_code == 200
    assert [event["type"] for event in events] == ["token", "tool_result", "done"]
    assert events[0]["content"] == "Kyoto works well for a four-day food-focused trip."
    assert events[1]["status"] == "awaiting_destination"
    assert repository.records["call-1"].status == "pending"
    assert repository.records["call-1"].partial_values == {"days": 4, "interests": "food"}
    assert queries and "food" in queries[0]
    assert session.messages[-1].extra_info["tool_result"]["status"] == "awaiting_destination"


@pytest.mark.asyncio
async def test_completed_result_invokes_supervisor_once_with_confirmed_fields(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    session, events = configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    calls = []

    async def supervisor(requirement, **kwargs):
        calls.append((requirement, kwargs))
        await kwargs["event_service"].emit(
            task_id=kwargs["task_id"],
            user_id=kwargs["user_id"],
            conversation_id=kwargs["conversation_id"],
            event_type="task_completed",
        )
        return FakeDraft(requirement.destination)

    monkeypatch.setattr(tools, "run_travel_planning", supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        response = await client.post("/api/v1/chat/tools/call-1/result", json=payload)

    stream = parse_sse(response)
    assert [event["type"] for event in stream] == ["result", "token", "done"]
    assert len(calls) == 1
    requirement, kwargs = calls[0]
    assert isinstance(requirement, TravelRequirement)
    assert requirement.destination == "Kyoto"
    assert requirement.departure_date == date(2026, 8, 3)
    assert requirement.days == 4
    assert kwargs["task_id"] == "call-1"
    assert repository.records["call-1"].result == payload["result"]
    assert events.events[-1].event_type == "task_completed"
    assert session.messages[-1].extra_info["tool_result"]["status"] == "completed"
    assert session.messages[-1].extra_info["assistant_result"] == stream[0]["payload"]["result"]


@pytest.mark.asyncio
async def test_duplicate_completed_result_uses_stored_completion_without_supervisor(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    confirmed = {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4}
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id), status="completed")])
    repository.records["call-1"].result = confirmed
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    async def unexpected_supervisor(*_args, **_kwargs):
        raise AssertionError("duplicate completion must not invoke Supervisor")

    monkeypatch.setattr(tools, "run_travel_planning", unexpected_supervisor, raising=False)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": confirmed},
        )

    stream = parse_sse(response)
    assert response.status_code == 200
    assert stream[0]["type"] == "result"
    assert stream[0]["payload"]["result"] == confirmed
```

# State Machine Fix Addendum

## Current app/api/v1/tools.py
```
"""Tool-result SSE API."""

import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.agents.supervisor import run_travel_planning
from app.api.dependencies import get_current_user
from app.core.checkpointer import get_checkpointer
from app.governance.events import TaskEventService
from app.governance.postgres import PostgresEventRepository
from app.governance.tool_invocations import PostgresToolInvocationRepository
from app.models.base import async_session_maker
from app.models.message import Message
from app.models.user import User
from app.schemas.events import SSEEvent
from app.schemas.planning import TravelRequirement
from app.schemas.tools import ToolResultRequest
from app.services.open_qa import answer_open_question
from app.services.planning import render_plan_markdown


router = APIRouter(prefix="/chat/tools", tags=["chat tools"])
PROCESSING_LEASE_TIMEOUT = timedelta(minutes=2)


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def save_assistant_message(conversation_id: str, content: str, extra_info: dict) -> None:
    async with async_session_maker() as db:
        message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            extra_info=extra_info,
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)


def recommendation_query(partial_values: dict) -> str:
    values = json.dumps(partial_values, ensure_ascii=False, sort_keys=True)
    return f"Recommend a travel destination based on these confirmed preferences: {values}"


async def existing_completion_stream(call_id: str, conversation_id: str, result: dict | None):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    durable = result if isinstance(result, dict) else {}
    task_id = durable.get("task_id", call_id)
    assistant_result = durable.get("assistant_result", result)
    assistant_markdown = durable.get(
        "assistant_markdown", "A travel-planning task has already been submitted."
    )
    yield sse(
        event(
            "result",
            {"task_id": task_id, "status": "completed", "result": assistant_result},
        )
    )
    yield sse(event("token", {"content": assistant_markdown}))
    yield sse(event("done"))


async def processing_stream(call_id: str, conversation_id: str):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    yield sse(
        event(
            "tool_result",
            {
                "tool": "collect_trip_requirements",
                "status": "processing",
                "terminal": False,
            },
        )
    )
    yield sse(event("done"))


async def tool_result_stream(call_id: str, data: ToolResultRequest, user_id: str, record):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=record.conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    claim_version = None
    try:
        repository = PostgresToolInvocationRepository()

        if data.status == "recommend_destination":
            if record.status == "processing":
                async for frame in processing_stream(call_id, record.conversation_id):
                    yield frame
                return
            updated = await repository.update_partial(call_id, user_id, data.partial_values)
            if updated is None:
                raise ValueError("Tool invocation is unavailable")

            answer = await answer_open_question(recommendation_query(updated.partial_values))
            tool_result = {
                "tool": data.tool,
                "status": "awaiting_destination",
                "partial_values": updated.partial_values,
            }
            await save_assistant_message(
                record.conversation_id,
                answer,
                {"tool_result": tool_result},
            )
            yield sse(event("token", {"content": answer}))
            yield sse(event("tool_result", tool_result))
            yield sse(event("done"))
            return

        if data.status != "completed" or data.result is None:
            raise ValueError("A completed tool result requires destination, departure_date, and days")

        confirmed_result = data.result.model_dump(mode="json")
        claim = await repository.claim_processing(
            call_id, user_id, PROCESSING_LEASE_TIMEOUT
        )
        if claim is None:
            raise ValueError("Tool invocation is unavailable")
        if not claim.claimed:
            if claim.record.status == "completed":
                async for frame in existing_completion_stream(
                    call_id, claim.record.conversation_id, claim.record.result
                ):
                    yield frame
            elif claim.record.status == "processing":
                async for frame in processing_stream(call_id, claim.record.conversation_id):
                    yield frame
            else:
                raise ValueError("Tool invocation is not available for processing")
            return

        claim_version = claim.claim_version
        record = claim.record
        requirement = TravelRequirement(**data.result.model_dump())
        event_service = TaskEventService(PostgresEventRepository())
        draft = await run_travel_planning(
            requirement,
            checkpointer=await get_checkpointer(),
            event_service=event_service,
            task_id=call_id,
            user_id=user_id,
            conversation_id=record.conversation_id,
        )
        assistant_result = draft.model_dump(mode="json")
        assistant_content = json.dumps(assistant_result, ensure_ascii=False)
        if hasattr(draft, "itinerary") and hasattr(draft, "requirement"):
            assistant_content = render_plan_markdown(draft)
        durable_result = {
            "confirmed_result": confirmed_result,
            "task_id": call_id,
            "assistant_result": assistant_result,
            "assistant_markdown": assistant_content,
            "draft": assistant_result,
            "route": getattr(draft, "route", None),
        }
        tool_result = {
            "tool": data.tool,
            "status": "completed",
            "result": confirmed_result,
            "task_id": call_id,
        }
        async with async_session_maker() as db:
            async with db.begin():
                finished = await repository.finish_processing(
                    call_id,
                    user_id,
                    claim_version,
                    durable_result,
                    session=db,
                )
                if finished is None:
                    raise ValueError("Tool invocation processing claim was lost")
                db.add(
                    Message(
                        conversation_id=finished.conversation_id,
                        role="assistant",
                        content=assistant_content,
                        extra_info={
                            "tool_result": tool_result,
                            "assistant_result": assistant_result,
                        },
                    )
                )
        claim_version = None
        yield sse(event("result", {"task_id": call_id, "status": "completed", "result": assistant_result}))
        yield sse(event("token", {"content": assistant_content}))
        yield sse(event("done"))
    except Exception:
        if claim_version is not None:
            try:
                await repository.release_processing(call_id, user_id, claim_version)
            except Exception:
                pass
        yield sse(
            event(
                "error",
                {
                    "code": "internal_error",
                    "message": "Travel planning is temporarily unavailable. Please retry.",
                    "retryable": True,
                },
            )
        )
        yield sse(event("done"))


@router.post("/{call_id}/result")
async def submit_tool_result(
    call_id: str,
    data: ToolResultRequest,
    user: User = Depends(get_current_user),
):
    user_id = str(user.id)
    repository = PostgresToolInvocationRepository()
    record = await repository.get_for_user(call_id, user_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool invocation not found")
    if record.tool != data.tool:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tool does not match invocation")
    if data.status == "completed" and data.result is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Completed results require destination, departure_date, and days",
        )
    if data.status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cancelled tool results are not supported",
        )
    if record.status == "completed" and data.status == "completed":
        return StreamingResponse(
            existing_completion_stream(call_id, record.conversation_id, record.result),
            media_type="text/event-stream",
        )
    if record.status not in {"pending", "processing"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tool invocation is not pending")

    return StreamingResponse(
        tool_result_stream(call_id, data, user_id, record),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

## Current app/governance/tool_invocations.py
```
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import async_session_maker
from app.models.conversation import Conversation
from app.models.tool_invocation import ToolInvocation


DEFAULT_PROCESSING_LEASE_TIMEOUT = timedelta(minutes=2)


def _lease_delta(lease_timeout: timedelta | int | float) -> timedelta:
    return (
        lease_timeout
        if isinstance(lease_timeout, timedelta)
        else timedelta(seconds=lease_timeout)
    )


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


class ProcessingOutcome(BaseModel):
    record: ToolInvocationRecord
    claimed: bool
    claim_version: int


class ToolInvocationRepository(Protocol):
    async def create(self, record: ToolInvocationRecord) -> ToolInvocationRecord: ...
    async def get_for_user(self, call_id: str, user_id: str) -> ToolInvocationRecord | None: ...
    async def update_partial(
        self, call_id: str, user_id: str, partial_values: dict[str, Any]
    ) -> ToolInvocationRecord | None: ...
    async def complete_once(
        self, call_id: str, user_id: str, result: dict[str, Any] | None
    ) -> CompletionOutcome | None: ...
    async def claim_processing(
        self,
        call_id: str,
        user_id: str,
        lease_timeout: timedelta | int | float = DEFAULT_PROCESSING_LEASE_TIMEOUT,
    ) -> ProcessingOutcome | None: ...
    async def finish_processing(
        self,
        call_id: str,
        user_id: str,
        expected_version: int,
        durable_result: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> ToolInvocationRecord | None: ...
    async def release_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> ToolInvocationRecord | None: ...


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
            if record is None or record.user_id != user_id or record.status != "pending":
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

    async def claim_processing(
        self,
        call_id: str,
        user_id: str,
        lease_timeout: timedelta | int | float = DEFAULT_PROCESSING_LEASE_TIMEOUT,
    ) -> ProcessingOutcome | None:
        async with self._lock:
            record = self.records.get(call_id)
            if record is None or record.user_id != user_id:
                return None
            now = datetime.now(timezone.utc)
            lease_delta = _lease_delta(lease_timeout)
            stale = (
                record.status == "processing"
                and now - record.updated_at >= lease_delta
            )
            can_claim = record.status == "pending" or stale
            if not can_claim:
                return ProcessingOutcome(
                    record=record.model_copy(deep=True),
                    claimed=False,
                    claim_version=record.version,
                )
            record.status = "processing"
            record.version += 1
            record.updated_at = now
            return ProcessingOutcome(
                record=record.model_copy(deep=True),
                claimed=True,
                claim_version=record.version,
            )

    async def finish_processing(
        self,
        call_id: str,
        user_id: str,
        expected_version: int,
        durable_result: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> ToolInvocationRecord | None:
        async with self._lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "completed"
            record.result = deepcopy(durable_result)
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def release_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> ToolInvocationRecord | None:
        async with self._lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "pending"
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)


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
                    ToolInvocation.status == "pending",
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

    async def claim_processing(
        self,
        call_id: str,
        user_id: str,
        lease_timeout: timedelta | int | float = DEFAULT_PROCESSING_LEASE_TIMEOUT,
    ) -> ProcessingOutcome | None:
        now = datetime.now(timezone.utc)
        cutoff = now - _lease_delta(lease_timeout)
        async with self.session_factory() as session, session.begin():
            claim = await session.execute(
                update(ToolInvocation)
                .where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                    or_(
                        ToolInvocation.status == "pending",
                        and_(
                            ToolInvocation.status == "processing",
                            ToolInvocation.updated_at <= cutoff,
                        ),
                    ),
                )
                .values(
                    status="processing",
                    version=ToolInvocation.version + 1,
                    updated_at=now,
                )
                .returning(ToolInvocation)
            )
            entity = claim.scalar_one_or_none()
            if entity is not None:
                return ProcessingOutcome(
                    record=self._record_from_entity(entity),
                    claimed=True,
                    claim_version=entity.version,
                )
            entity = await session.scalar(
                select(ToolInvocation).where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                )
            )
            return (
                ProcessingOutcome(
                    record=self._record_from_entity(entity),
                    claimed=False,
                    claim_version=entity.version,
                )
                if entity is not None
                else None
            )

    async def finish_processing(
        self,
        call_id: str,
        user_id: str,
        expected_version: int,
        durable_result: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> ToolInvocationRecord | None:
        now = datetime.now(timezone.utc)
        statement = (
            update(ToolInvocation)
            .where(
                ToolInvocation.call_id == call_id,
                ToolInvocation.user_id == UUID(user_id),
                ToolInvocation.status == "processing",
                ToolInvocation.version == expected_version,
            )
            .values(
                status="completed",
                result=deepcopy(durable_result),
                version=ToolInvocation.version + 1,
                updated_at=now,
            )
            .returning(ToolInvocation)
        )
        if session is not None:
            result = await session.execute(statement)
            entity = result.scalar_one_or_none()
            return self._record_from_entity(entity) if entity is not None else None
        async with self.session_factory() as owned_session, owned_session.begin():
            result = await owned_session.execute(statement)
            entity = result.scalar_one_or_none()
            return self._record_from_entity(entity) if entity is not None else None

    async def release_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> ToolInvocationRecord | None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session, session.begin():
            result = await session.execute(
                update(ToolInvocation)
                .where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                    ToolInvocation.status == "processing",
                    ToolInvocation.version == expected_version,
                )
                .values(
                    status="pending",
                    version=ToolInvocation.version + 1,
                    updated_at=now,
                )
                .returning(ToolInvocation)
            )
            entity = result.scalar_one_or_none()
            return self._record_from_entity(entity) if entity is not None else None

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

## Current app/schemas/tools.py
```
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


MainAgentAction = Literal[
    "collect_trip_requirements",
    "answer_open_question",
    "recommend_destination",
    "direct_response",
]


class MainAgentDecision(BaseModel):
    action: MainAgentAction
    reason: str
    response: str | None = None
    initial_values: dict[str, Any] = Field(default_factory=dict)


class TripFormArguments(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    destination: str = Field(min_length=1, max_length=80)
    departure_date: date
    days: int = Field(ge=1, le=30)


class TripFormResult(TripFormArguments):
    pass


class ToolCallPayload(BaseModel):
    call_id: str
    tool: Literal["collect_trip_requirements"]
    arguments: dict[str, Any]


class ToolResultRequest(BaseModel):
    tool: Literal["collect_trip_requirements"]
    status: Literal["completed", "recommend_destination", "cancelled"]
    result: TripFormResult | None = None
    partial_values: dict[str, Any] = Field(default_factory=dict)
```

## Current tests/test_trip_form_tool_flow.py
```
import json
import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.dependencies import get_current_user
from app.governance.tool_invocations import CompletionOutcome, ToolInvocationRecord
from app.main import app
from app.schemas.planning import TravelRequirement


class InMemoryInvocationRepository:
    def __init__(self, records):
        self.records = {record.call_id: record.model_copy(deep=True) for record in records}
        self.lock = asyncio.Lock()

    async def get_for_user(self, call_id, user_id):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        return record.model_copy(deep=True)

    async def update_partial(self, call_id, user_id, partial_values):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        record.partial_values = {**record.partial_values, **partial_values}
        record.version += 1
        return record.model_copy(deep=True)

    async def complete_once(self, call_id, user_id, result):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        if record.status == "completed":
            return CompletionOutcome(record=record.model_copy(deep=True), completed_now=False)
        record.status = "completed"
        record.result = result
        record.version += 1
        return CompletionOutcome(record=record.model_copy(deep=True), completed_now=True)

    async def claim_processing(self, call_id, user_id, lease_timeout):
        async with self.lock:
            record = self.records.get(call_id)
            if record is None or record.user_id != user_id:
                return None
            stale = (
                record.status == "processing"
                and datetime.now(timezone.utc) - record.updated_at >= lease_timeout
            )
            if record.status not in {"pending", "processing"} or (
                record.status == "processing" and not stale
            ):
                from app.governance.tool_invocations import ProcessingOutcome

                return ProcessingOutcome(
                    record=record.model_copy(deep=True),
                    claimed=False,
                    claim_version=record.version,
                )
            record.status = "processing"
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            from app.governance.tool_invocations import ProcessingOutcome

            return ProcessingOutcome(
                record=record.model_copy(deep=True),
                claimed=True,
                claim_version=record.version,
            )

    async def finish_processing(self, call_id, user_id, expected_version, durable_result, session=None):
        async with self.lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "completed"
            record.result = durable_result
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def release_processing(self, call_id, user_id, expected_version):
        async with self.lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "pending"
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)


class RecordingSession:
    def __init__(self):
        self.messages = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def add(self, message):
        self.messages.append(message)

    async def commit(self):
        return None

    async def refresh(self, _message):
        return None

    def begin(self):
        return self


class RecordingEventRepository:
    def __init__(self):
        self.events = []

    async def append(self, event):
        event.sequence = len(self.events) + 1
        self.events.append(event)
        return event


class FakeDraft:
    def __init__(self, destination):
        self.destination = destination

    def model_dump(self, *, mode):
        assert mode == "json"
        return {"destination": self.destination, "summary": "confirmed itinerary"}


def invocation(*, user_id, status="pending", tool="collect_trip_requirements"):
    return ToolInvocationRecord(
        call_id="call-1",
        user_id=user_id,
        conversation_id=str(uuid4()),
        tool=tool,
        status=status,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def parse_sse(response):
    return [
        json.loads(frame.removeprefix("data: ").strip())
        for frame in response.text.split("\n\n")
        if frame.strip()
    ]


@asynccontextmanager
async def endpoint_client(user):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def configure_endpoint(monkeypatch, repository):
    from app.api.v1 import tools

    session = RecordingSession()
    events = RecordingEventRepository()
    monkeypatch.setattr(tools, "PostgresToolInvocationRepository", lambda: repository, raising=False)
    monkeypatch.setattr(tools, "PostgresEventRepository", lambda: events, raising=False)
    monkeypatch.setattr(tools, "async_session_maker", lambda: session, raising=False)

    async def checkpointer():
        return None

    monkeypatch.setattr(tools, "get_checkpointer", checkpointer, raising=False)
    return session, events


def test_trip_form_result_route_is_registered():
    paths = {route.path for route in app.routes}

    assert "/api/v1/chat/tools/{call_id}/result" in paths


@pytest.mark.asyncio
async def test_tool_result_rejects_another_users_call(monkeypatch):
    owner_id = str(uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=owner_id)])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(SimpleNamespace(id=uuid4())) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": {
                "destination": "Kyoto", "departure_date": "2026-08-03", "days": 4,
            }},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        None,
        {"destination": "Kyoto", "days": 4},
        {"destination": "Kyoto", "departure_date": "invalid", "days": 4},
        {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 0},
        {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4, "extra": True},
    ],
)
async def test_completed_result_requires_valid_trip_fields(monkeypatch, result):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": result},
        )

    assert response.status_code == 422
    assert repository.records["call-1"].status == "pending"


@pytest.mark.asyncio
async def test_tool_result_rejects_wrong_stored_tool(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository(
        [invocation(user_id=str(user.id), tool="other_tool")]
    )
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "recommend_destination"},
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_recommendation_requires_a_pending_call(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id), status="cancelled")])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "recommend_destination"},
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_recommendation_keeps_call_pending_and_returns_rag_answer(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    session, _ = configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    queries = []

    async def recommend(query):
        queries.append(query)
        return "Kyoto works well for a four-day food-focused trip."

    monkeypatch.setattr(tools, "answer_open_question", recommend, raising=False)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={
                "tool": "collect_trip_requirements",
                "status": "recommend_destination",
                "partial_values": {"days": 4, "interests": "food"},
            },
        )

    events = parse_sse(response)
    assert response.status_code == 200
    assert [event["type"] for event in events] == ["token", "tool_result", "done"]
    assert events[0]["content"] == "Kyoto works well for a four-day food-focused trip."
    assert events[1]["status"] == "awaiting_destination"
    assert repository.records["call-1"].status == "pending"
    assert repository.records["call-1"].partial_values == {"days": 4, "interests": "food"}
    assert queries and "food" in queries[0]
    assert session.messages[-1].extra_info["tool_result"]["status"] == "awaiting_destination"


@pytest.mark.asyncio
async def test_completed_result_invokes_supervisor_once_with_confirmed_fields(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    session, events = configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    calls = []

    async def supervisor(requirement, **kwargs):
        calls.append((requirement, kwargs))
        await kwargs["event_service"].emit(
            task_id=kwargs["task_id"],
            user_id=kwargs["user_id"],
            conversation_id=kwargs["conversation_id"],
            event_type="task_completed",
        )
        return FakeDraft(requirement.destination)

    monkeypatch.setattr(tools, "run_travel_planning", supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        response = await client.post("/api/v1/chat/tools/call-1/result", json=payload)
        duplicate_response = await client.post(
            "/api/v1/chat/tools/call-1/result", json=payload
        )

    stream = parse_sse(response)
    duplicate_stream = parse_sse(duplicate_response)
    assert [event["type"] for event in stream] == ["result", "token", "done"]
    assert len(calls) == 1
    requirement, kwargs = calls[0]
    assert isinstance(requirement, TravelRequirement)
    assert requirement.destination == "Kyoto"
    assert requirement.departure_date == date(2026, 8, 3)
    assert requirement.days == 4
    assert kwargs["task_id"] == "call-1"
    durable = repository.records["call-1"].result
    assert durable["confirmed_result"] == payload["result"]
    assert durable["task_id"] == "call-1"
    assert [
        (event["type"], event.get("payload")) for event in duplicate_stream
    ] == [
        (event["type"], event.get("payload")) for event in stream
    ]
    assert events.events[-1].event_type == "task_completed"
    assert session.messages[-1].extra_info["tool_result"]["status"] == "completed"
    assert session.messages[-1].extra_info["assistant_result"] == stream[0]["payload"]["result"]


@pytest.mark.asyncio
async def test_duplicate_completed_result_uses_stored_completion_without_supervisor(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    confirmed = {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4}
    durable = {
        "confirmed_result": confirmed,
        "task_id": "call-1",
        "assistant_result": {"destination": "Kyoto", "summary": "confirmed itinerary"},
        "assistant_markdown": '{"destination": "Kyoto", "summary": "confirmed itinerary"}',
    }
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id), status="completed")])
    repository.records["call-1"].result = durable
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    async def unexpected_supervisor(*_args, **_kwargs):
        raise AssertionError("duplicate completion must not invoke Supervisor")

    monkeypatch.setattr(tools, "run_travel_planning", unexpected_supervisor, raising=False)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": confirmed},
        )

    stream = parse_sse(response)
    assert response.status_code == 200
    assert stream[0]["type"] == "result"
    assert stream[0]["payload"]["result"] == durable["assistant_result"]


@pytest.mark.asyncio
async def test_supervisor_failure_releases_claim_and_sanitizes_retryable_error(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    async def failing_supervisor(*_args, **_kwargs):
        raise RuntimeError("database password leaked")

    monkeypatch.setattr(tools, "run_travel_planning", failing_supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        response = await client.post("/api/v1/chat/tools/call-1/result", json=payload)

    events = parse_sse(response)
    assert [event["type"] for event in events] == ["error", "done"]
    assert events[0]["payload"]["retryable"] is True
    assert "database password leaked" not in json.dumps(events[0])
    assert repository.records["call-1"].status == "pending"

    async def successful_supervisor(*_args, **_kwargs):
        return FakeDraft("Kyoto")

    monkeypatch.setattr(tools, "run_travel_planning", successful_supervisor, raising=False)

    async with endpoint_client(user) as client:
        retry = await client.post("/api/v1/chat/tools/call-1/result", json=payload)

    assert parse_sse(retry)[0]["type"] == "result"


@pytest.mark.asyncio
async def test_active_processing_duplicate_returns_nonterminal_tool_result(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    active = invocation(user_id=str(user.id), status="processing")
    active.updated_at = datetime.now(timezone.utc)
    repository = InMemoryInvocationRepository([active])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    async def unexpected_supervisor(*_args, **_kwargs):
        raise AssertionError("active processing duplicate must not invoke Supervisor")

    monkeypatch.setattr(tools, "run_travel_planning", unexpected_supervisor, raising=False)
    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={
                "tool": "collect_trip_requirements",
                "status": "completed",
                "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
            },
        )

    events = parse_sse(response)
    assert [event["type"] for event in events] == ["tool_result", "done"]
    assert events[0]["status"] == "processing"
    assert events[0]["payload"]["terminal"] is False
```

## Current tests/test_tool_invocations.py
```
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.governance.tool_invocations import (
    InMemoryToolInvocationRepository,
    PostgresToolInvocationRepository,
    ToolInvocationRecord,
)
from app.models.base import Base
import app.models  # noqa: F401


class FakeExecutionResult:
    def __init__(self, entity):
        self.entity = entity

    def scalar_one_or_none(self):
        return self.entity


class FakeSession:
    def __init__(self, *, scalar_results=(), update_entity=None):
        self.scalar_results = list(scalar_results)
        self.update_entity = update_entity
        self.added = []
        self.scalar_statements = []
        self.executed_statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return self

    async def scalar(self, statement):
        self.scalar_statements.append(statement)
        return self.scalar_results.pop(0)

    async def execute(self, statement):
        self.executed_statements.append(statement)
        return FakeExecutionResult(self.update_entity)

    def add(self, entity):
        self.added.append(entity)


class FakeSessionFactory:
    def __init__(self, *sessions):
        self.sessions = list(sessions)

    def __call__(self):
        return self.sessions.pop(0)


def postgres_entity(*, user_id, conversation_id, result, version=2, status="completed"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        call_id="c1",
        user_id=user_id,
        conversation_id=conversation_id,
        tool="collect_trip_requirements",
        status=status,
        arguments={"initial_values": {}},
        partial_values={},
        result=result,
        version=version,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_tool_result_is_idempotent():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
        )
    )

    first = await repository.complete_once(
        "c1",
        "u1",
        {"destination": "Chengdu", "departure_date": "2026-08-10", "days": 4},
    )
    second = await repository.complete_once("c1", "u1", first.record.result)

    assert first.completed_now is True
    assert first.record.status == "completed"
    assert second.completed_now is False
    assert second.record.version == first.record.version


@pytest.mark.asyncio
async def test_duplicate_completion_keeps_the_first_result():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
        )
    )

    first = await repository.complete_once("c1", "u1", {"destination": "Chengdu"})
    duplicate = await repository.complete_once("c1", "u1", {"destination": "Beijing"})

    assert duplicate.completed_now is False
    assert duplicate.record.result == first.record.result == {"destination": "Chengdu"}


@pytest.mark.asyncio
async def test_repository_records_are_isolated_from_caller_mutation():
    repository = InMemoryToolInvocationRepository()
    record = ToolInvocationRecord(
        call_id="c1",
        user_id="u1",
        conversation_id="v1",
        tool="collect_trip_requirements",
        arguments={"initial_values": {"destination": "Chengdu"}},
    )
    await repository.create(record)
    record.arguments["initial_values"]["destination"] = "Beijing"

    stored = await repository.get_for_user("c1", "u1")
    stored.arguments["initial_values"]["destination"] = "Shanghai"

    completed = await repository.complete_once("c1", "u1", {"days": [4]})
    completed.record.result["days"].append(5)
    reloaded = await repository.get_for_user("c1", "u1")

    assert reloaded.arguments == {"initial_values": {"destination": "Chengdu"}}
    assert reloaded.result == {"days": [4]}


@pytest.mark.asyncio
async def test_tool_call_is_user_scoped():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
        )
    )

    assert await repository.get_for_user("c1", "u2") is None


@pytest.mark.asyncio
async def test_partial_values_are_merged_for_the_owner_only():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
            partial_values={"destination": "Chengdu"},
        )
    )

    updated = await repository.update_partial("c1", "u1", {"days": 4})

    assert updated is not None
    assert updated.partial_values == {"destination": "Chengdu", "days": 4}
    assert await repository.update_partial("c1", "u2", {"days": 5}) is None


@pytest.mark.asyncio
async def test_partial_values_do_not_update_a_non_pending_call():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
            status="processing",
        )
    )

    assert await repository.update_partial("c1", "u1", {"days": 4}) is None
    stored = await repository.get_for_user("c1", "u1")
    assert stored.partial_values == {}


@pytest.mark.asyncio
async def test_processing_claim_has_one_concurrent_winner():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements"
        )
    )

    outcomes = await asyncio.gather(
        repository.claim_processing("c1", "u1", timedelta(seconds=30)),
        repository.claim_processing("c1", "u1", timedelta(seconds=30)),
    )

    assert sum(outcome.claimed for outcome in outcomes) == 1
    assert all(outcome.record.status == "processing" for outcome in outcomes)
    winner = next(outcome for outcome in outcomes if outcome.claimed)
    loser = next(outcome for outcome in outcomes if not outcome.claimed)
    assert winner.claim_version == loser.claim_version


@pytest.mark.asyncio
async def test_stale_processing_claim_can_be_reclaimed():
    repository = InMemoryToolInvocationRepository()
    record = ToolInvocationRecord(
        call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements",
        status="processing", version=4,
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    await repository.create(record)

    outcome = await repository.claim_processing("c1", "u1", timedelta(seconds=30))

    assert outcome.claimed is True
    assert outcome.claim_version == 5
    assert outcome.record.status == "processing"


@pytest.mark.asyncio
async def test_processing_finish_and_release_require_matching_claim_version():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements"
        )
    )
    claim = await repository.claim_processing("c1", "u1", timedelta(seconds=30))

    assert await repository.finish_processing("c1", "u1", claim.claim_version - 1, {}) is None
    released = await repository.release_processing("c1", "u1", claim.claim_version)
    assert released.status == "pending"
    assert await repository.finish_processing("c1", "u1", claim.claim_version, {}) is None


def test_tool_invocation_model_is_registered():
    assert "tool_invocation" in Base.metadata.tables


@pytest.mark.asyncio
async def test_postgres_create_rejects_conversation_not_owned_by_user():
    session = FakeSession(scalar_results=[None])
    repository = PostgresToolInvocationRepository(FakeSessionFactory(session))
    record = ToolInvocationRecord(
        call_id="c1",
        user_id=str(uuid4()),
        conversation_id=str(uuid4()),
        tool="collect_trip_requirements",
    )

    with pytest.raises(PermissionError):
        await repository.create(record)

    assert session.added == []
    assert len(session.scalar_statements) == 1


@pytest.mark.asyncio
async def test_postgres_create_in_session_uses_the_callers_transaction_and_checks_ownership():
    user_id = uuid4()
    conversation_id = uuid4()
    session = FakeSession(scalar_results=[conversation_id])
    repository = PostgresToolInvocationRepository()
    record = ToolInvocationRecord(
        call_id="c1",
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        tool="collect_trip_requirements",
        arguments={"initial_values": {"destination": "Chengdu"}},
    )

    created = await repository.create_in_session(session, record)

    assert created == record
    assert len(session.scalar_statements) == 1
    assert len(session.added) == 1
    assert session.added[0].call_id == "c1"
    assert session.added[0].user_id == user_id
    assert session.added[0].conversation_id == conversation_id


@pytest.mark.asyncio
async def test_postgres_completion_marks_only_returned_update_row_as_newly_completed():
    user_id = uuid4()
    conversation_id = uuid4()
    result = {"destination": "Chengdu"}
    claimed_session = FakeSession(
        update_entity=postgres_entity(
            user_id=user_id,
            conversation_id=conversation_id,
            result=result,
        )
    )
    duplicate_session = FakeSession(
        scalar_results=[
            postgres_entity(
                user_id=user_id,
                conversation_id=conversation_id,
                result=result,
            )
        ]
    )
    repository = PostgresToolInvocationRepository(
        FakeSessionFactory(claimed_session, duplicate_session)
    )

    claimed = await repository.complete_once("c1", str(user_id), result)
    duplicate = await repository.complete_once("c1", str(user_id), {"destination": "Beijing"})

    assert claimed.completed_now is True
    assert duplicate.completed_now is False
    assert duplicate.record.result == result
    assert len(claimed_session.executed_statements) == 1
    assert len(duplicate_session.executed_statements) == 1
    completion_sql = str(
        claimed_session.executed_statements[0].compile(dialect=postgresql.dialect())
    )
    assert "tool_invocation.call_id =" in completion_sql
    assert "tool_invocation.user_id =" in completion_sql
    assert "tool_invocation.status !=" in completion_sql


@pytest.mark.asyncio
async def test_postgres_processing_claim_uses_pending_or_stale_lease_predicate():
    user_id = uuid4()
    conversation_id = uuid4()
    session = FakeSession(
        update_entity=postgres_entity(
            user_id=user_id,
            conversation_id=conversation_id,
            result=None,
            version=3,
            status="processing",
        )
    )
    repository = PostgresToolInvocationRepository(FakeSessionFactory(session))

    outcome = await repository.claim_processing("c1", str(user_id), timedelta(seconds=30))

    assert outcome.claimed is True
    assert outcome.claim_version == 3
    claim_sql = str(session.executed_statements[0].compile(dialect=postgresql.dialect()))
    assert "tool_invocation.status" in claim_sql
    assert "tool_invocation.updated_at <=" in claim_sql
    assert "tool_invocation.version +" in claim_sql


@pytest.mark.asyncio
async def test_postgres_finish_and_release_require_processing_version_match():
    user_id = uuid4()
    conversation_id = uuid4()
    finish_session = FakeSession(
        update_entity=postgres_entity(
            user_id=user_id,
            conversation_id=conversation_id,
            result={"task_id": "c1"},
            version=5,
            status="completed",
        )
    )
    release_session = FakeSession(
        update_entity=postgres_entity(
            user_id=user_id,
            conversation_id=conversation_id,
            result=None,
            version=5,
            status="pending",
        )
    )
    repository = PostgresToolInvocationRepository(
        FakeSessionFactory(finish_session, release_session)
    )

    finished = await repository.finish_processing("c1", str(user_id), 4, {"task_id": "c1"})
    released = await repository.release_processing("c1", str(user_id), 4)

    assert finished.status == "completed"
    assert released.status == "pending"
    finish_sql = str(finish_session.executed_statements[0].compile(dialect=postgresql.dialect()))
    release_sql = str(release_session.executed_statements[0].compile(dialect=postgresql.dialect()))
    assert "tool_invocation.version =" in finish_sql
    assert "tool_invocation.status =" in finish_sql
    assert "tool_invocation.version =" in release_sql
```

# Heartbeat Addendum

## Current app/api/v1/tools.py
```
"""Tool-result SSE API."""

import asyncio
import json
from contextlib import suppress
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.agents.supervisor import run_travel_planning
from app.api.dependencies import get_current_user
from app.core.checkpointer import get_checkpointer
from app.governance.events import TaskEventService
from app.governance.postgres import PostgresEventRepository
from app.governance.tool_invocations import PostgresToolInvocationRepository
from app.models.base import async_session_maker
from app.models.message import Message
from app.models.user import User
from app.schemas.events import SSEEvent
from app.schemas.planning import TravelRequirement
from app.schemas.tools import ToolResultRequest
from app.services.open_qa import answer_open_question
from app.services.planning import render_plan_markdown


router = APIRouter(prefix="/chat/tools", tags=["chat tools"])
PROCESSING_LEASE_TIMEOUT = timedelta(minutes=2)


class ProcessingLeaseLostError(RuntimeError):
    pass


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def save_assistant_message(conversation_id: str, content: str, extra_info: dict) -> None:
    async with async_session_maker() as db:
        message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            extra_info=extra_info,
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)


def recommendation_query(partial_values: dict) -> str:
    values = json.dumps(partial_values, ensure_ascii=False, sort_keys=True)
    return f"Recommend a travel destination based on these confirmed preferences: {values}"


async def processing_heartbeat(
    repository,
    call_id: str,
    user_id: str,
    claim_version: int,
    lease_timeout: timedelta,
    lease_lost: asyncio.Event,
) -> None:
    interval = max(lease_timeout.total_seconds() / 3, 0.01)
    try:
        while True:
            await asyncio.sleep(interval)
            if not await repository.renew_processing(call_id, user_id, claim_version):
                lease_lost.set()
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        lease_lost.set()


async def stop_processing_heartbeat(heartbeat_task: asyncio.Task | None) -> None:
    if heartbeat_task is None:
        return
    heartbeat_task.cancel()
    with suppress(asyncio.CancelledError):
        await heartbeat_task


async def existing_completion_stream(call_id: str, conversation_id: str, result: dict | None):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    durable = result if isinstance(result, dict) else {}
    task_id = durable.get("task_id", call_id)
    assistant_result = durable.get("assistant_result", result)
    assistant_markdown = durable.get(
        "assistant_markdown", "A travel-planning task has already been submitted."
    )
    yield sse(
        event(
            "result",
            {"task_id": task_id, "status": "completed", "result": assistant_result},
        )
    )
    yield sse(event("token", {"content": assistant_markdown}))
    yield sse(event("done"))


async def processing_stream(call_id: str, conversation_id: str):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    yield sse(
        event(
            "tool_result",
            {
                "tool": "collect_trip_requirements",
                "status": "processing",
                "terminal": False,
            },
        )
    )
    yield sse(event("done"))


async def tool_result_stream(call_id: str, data: ToolResultRequest, user_id: str, record):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=record.conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    claim_version = None
    repository = None
    heartbeat_task = None
    try:
        repository = PostgresToolInvocationRepository()

        if data.status == "recommend_destination":
            if record.status == "processing":
                async for frame in processing_stream(call_id, record.conversation_id):
                    yield frame
                return
            updated = await repository.update_partial(call_id, user_id, data.partial_values)
            if updated is None:
                raise ValueError("Tool invocation is unavailable")

            answer = await answer_open_question(recommendation_query(updated.partial_values))
            tool_result = {
                "tool": data.tool,
                "status": "awaiting_destination",
                "partial_values": updated.partial_values,
            }
            await save_assistant_message(
                record.conversation_id,
                answer,
                {"tool_result": tool_result},
            )
            yield sse(event("token", {"content": answer}))
            yield sse(event("tool_result", tool_result))
            yield sse(event("done"))
            return

        if data.status != "completed" or data.result is None:
            raise ValueError("A completed tool result requires destination, departure_date, and days")

        confirmed_result = data.result.model_dump(mode="json")
        claim = await repository.claim_processing(
            call_id, user_id, PROCESSING_LEASE_TIMEOUT
        )
        if claim is None:
            raise ValueError("Tool invocation is unavailable")
        if not claim.claimed:
            if claim.record.status == "completed":
                async for frame in existing_completion_stream(
                    call_id, claim.record.conversation_id, claim.record.result
                ):
                    yield frame
            elif claim.record.status == "processing":
                async for frame in processing_stream(call_id, claim.record.conversation_id):
                    yield frame
            else:
                raise ValueError("Tool invocation is not available for processing")
            return

        claim_version = claim.claim_version
        record = claim.record
        requirement = TravelRequirement(**data.result.model_dump())
        event_service = TaskEventService(PostgresEventRepository())
        lease_lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            processing_heartbeat(
                repository,
                call_id,
                user_id,
                claim_version,
                PROCESSING_LEASE_TIMEOUT,
                lease_lost,
            ),
            name=f"tool-result-heartbeat:{call_id}",
        )
        try:
            draft = await run_travel_planning(
                requirement,
                checkpointer=await get_checkpointer(),
                event_service=event_service,
                task_id=call_id,
                user_id=user_id,
                conversation_id=record.conversation_id,
            )
        finally:
            await stop_processing_heartbeat(heartbeat_task)
            heartbeat_task = None
        if lease_lost.is_set():
            raise ProcessingLeaseLostError("processing lease lost")
        assistant_result = draft.model_dump(mode="json")
        assistant_content = json.dumps(assistant_result, ensure_ascii=False)
        if hasattr(draft, "itinerary") and hasattr(draft, "requirement"):
            assistant_content = render_plan_markdown(draft)
        durable_result = {
            "confirmed_result": confirmed_result,
            "task_id": call_id,
            "assistant_result": assistant_result,
            "assistant_markdown": assistant_content,
            "draft": assistant_result,
            "route": getattr(draft, "route", None),
        }
        tool_result = {
            "tool": data.tool,
            "status": "completed",
            "result": confirmed_result,
            "task_id": call_id,
        }
        async with async_session_maker() as db:
            async with db.begin():
                finished = await repository.finish_processing(
                    call_id,
                    user_id,
                    claim_version,
                    durable_result,
                    session=db,
                )
                if finished is None:
                    raise ValueError("Tool invocation processing claim was lost")
                db.add(
                    Message(
                        conversation_id=finished.conversation_id,
                        role="assistant",
                        content=assistant_content,
                        extra_info={
                            "tool_result": tool_result,
                            "assistant_result": assistant_result,
                        },
                    )
                )
        claim_version = None
        yield sse(event("result", {"task_id": call_id, "status": "completed", "result": assistant_result}))
        yield sse(event("token", {"content": assistant_content}))
        yield sse(event("done"))
    except asyncio.CancelledError:
        await stop_processing_heartbeat(heartbeat_task)
        if repository is not None and claim_version is not None:
            with suppress(Exception):
                await repository.release_processing(call_id, user_id, claim_version)
        raise
    except Exception as exc:
        await stop_processing_heartbeat(heartbeat_task)
        if repository is not None and claim_version is not None:
            try:
                await repository.release_processing(call_id, user_id, claim_version)
            except Exception:
                pass
        yield sse(
            event(
                "error",
                {
                    "code": (
                        "processing_conflict"
                        if isinstance(exc, ProcessingLeaseLostError)
                        else "internal_error"
                    ),
                    "message": "Travel planning is temporarily unavailable. Please retry.",
                    "retryable": True,
                },
            )
        )
        yield sse(event("done"))


@router.post("/{call_id}/result")
async def submit_tool_result(
    call_id: str,
    data: ToolResultRequest,
    user: User = Depends(get_current_user),
):
    user_id = str(user.id)
    repository = PostgresToolInvocationRepository()
    record = await repository.get_for_user(call_id, user_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool invocation not found")
    if record.tool != data.tool:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tool does not match invocation")
    if data.status == "completed" and data.result is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Completed results require destination, departure_date, and days",
        )
    if data.status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cancelled tool results are not supported",
        )
    if record.status == "completed" and data.status == "completed":
        return StreamingResponse(
            existing_completion_stream(call_id, record.conversation_id, record.result),
            media_type="text/event-stream",
        )
    if record.status not in {"pending", "processing"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tool invocation is not pending")

    return StreamingResponse(
        tool_result_stream(call_id, data, user_id, record),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

## Current app/governance/tool_invocations.py
```
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import async_session_maker
from app.models.conversation import Conversation
from app.models.tool_invocation import ToolInvocation


DEFAULT_PROCESSING_LEASE_TIMEOUT = timedelta(minutes=2)


def _lease_delta(lease_timeout: timedelta | int | float) -> timedelta:
    return (
        lease_timeout
        if isinstance(lease_timeout, timedelta)
        else timedelta(seconds=lease_timeout)
    )


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


class ProcessingOutcome(BaseModel):
    record: ToolInvocationRecord
    claimed: bool
    claim_version: int


class ToolInvocationRepository(Protocol):
    async def create(self, record: ToolInvocationRecord) -> ToolInvocationRecord: ...
    async def get_for_user(self, call_id: str, user_id: str) -> ToolInvocationRecord | None: ...
    async def update_partial(
        self, call_id: str, user_id: str, partial_values: dict[str, Any]
    ) -> ToolInvocationRecord | None: ...
    async def complete_once(
        self, call_id: str, user_id: str, result: dict[str, Any] | None
    ) -> CompletionOutcome | None: ...
    async def claim_processing(
        self,
        call_id: str,
        user_id: str,
        lease_timeout: timedelta | int | float = DEFAULT_PROCESSING_LEASE_TIMEOUT,
    ) -> ProcessingOutcome | None: ...
    async def finish_processing(
        self,
        call_id: str,
        user_id: str,
        expected_version: int,
        durable_result: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> ToolInvocationRecord | None: ...
    async def release_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> ToolInvocationRecord | None: ...
    async def renew_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> bool: ...


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
            if record is None or record.user_id != user_id or record.status != "pending":
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

    async def claim_processing(
        self,
        call_id: str,
        user_id: str,
        lease_timeout: timedelta | int | float = DEFAULT_PROCESSING_LEASE_TIMEOUT,
    ) -> ProcessingOutcome | None:
        async with self._lock:
            record = self.records.get(call_id)
            if record is None or record.user_id != user_id:
                return None
            now = datetime.now(timezone.utc)
            lease_delta = _lease_delta(lease_timeout)
            stale = (
                record.status == "processing"
                and now - record.updated_at >= lease_delta
            )
            can_claim = record.status == "pending" or stale
            if not can_claim:
                return ProcessingOutcome(
                    record=record.model_copy(deep=True),
                    claimed=False,
                    claim_version=record.version,
                )
            record.status = "processing"
            record.version += 1
            record.updated_at = now
            return ProcessingOutcome(
                record=record.model_copy(deep=True),
                claimed=True,
                claim_version=record.version,
            )

    async def finish_processing(
        self,
        call_id: str,
        user_id: str,
        expected_version: int,
        durable_result: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> ToolInvocationRecord | None:
        async with self._lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "completed"
            record.result = deepcopy(durable_result)
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def release_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> ToolInvocationRecord | None:
        async with self._lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "pending"
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def renew_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> bool:
        async with self._lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return False
            record.updated_at = datetime.now(timezone.utc)
            return True


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
                    ToolInvocation.status == "pending",
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

    async def claim_processing(
        self,
        call_id: str,
        user_id: str,
        lease_timeout: timedelta | int | float = DEFAULT_PROCESSING_LEASE_TIMEOUT,
    ) -> ProcessingOutcome | None:
        now = datetime.now(timezone.utc)
        cutoff = now - _lease_delta(lease_timeout)
        async with self.session_factory() as session, session.begin():
            claim = await session.execute(
                update(ToolInvocation)
                .where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                    or_(
                        ToolInvocation.status == "pending",
                        and_(
                            ToolInvocation.status == "processing",
                            ToolInvocation.updated_at <= cutoff,
                        ),
                    ),
                )
                .values(
                    status="processing",
                    version=ToolInvocation.version + 1,
                    updated_at=now,
                )
                .returning(ToolInvocation)
            )
            entity = claim.scalar_one_or_none()
            if entity is not None:
                return ProcessingOutcome(
                    record=self._record_from_entity(entity),
                    claimed=True,
                    claim_version=entity.version,
                )
            entity = await session.scalar(
                select(ToolInvocation).where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                )
            )
            return (
                ProcessingOutcome(
                    record=self._record_from_entity(entity),
                    claimed=False,
                    claim_version=entity.version,
                )
                if entity is not None
                else None
            )

    async def finish_processing(
        self,
        call_id: str,
        user_id: str,
        expected_version: int,
        durable_result: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> ToolInvocationRecord | None:
        now = datetime.now(timezone.utc)
        statement = (
            update(ToolInvocation)
            .where(
                ToolInvocation.call_id == call_id,
                ToolInvocation.user_id == UUID(user_id),
                ToolInvocation.status == "processing",
                ToolInvocation.version == expected_version,
            )
            .values(
                status="completed",
                result=deepcopy(durable_result),
                version=ToolInvocation.version + 1,
                updated_at=now,
            )
            .returning(ToolInvocation)
        )
        if session is not None:
            result = await session.execute(statement)
            entity = result.scalar_one_or_none()
            return self._record_from_entity(entity) if entity is not None else None
        async with self.session_factory() as owned_session, owned_session.begin():
            result = await owned_session.execute(statement)
            entity = result.scalar_one_or_none()
            return self._record_from_entity(entity) if entity is not None else None

    async def release_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> ToolInvocationRecord | None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session, session.begin():
            result = await session.execute(
                update(ToolInvocation)
                .where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                    ToolInvocation.status == "processing",
                    ToolInvocation.version == expected_version,
                )
                .values(
                    status="pending",
                    version=ToolInvocation.version + 1,
                    updated_at=now,
                )
                .returning(ToolInvocation)
            )
            entity = result.scalar_one_or_none()
            return self._record_from_entity(entity) if entity is not None else None

    async def renew_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> bool:
        async with self.session_factory() as session, session.begin():
            result = await session.execute(
                update(ToolInvocation)
                .where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                    ToolInvocation.status == "processing",
                    ToolInvocation.version == expected_version,
                )
                .values(updated_at=datetime.now(timezone.utc))
                .returning(ToolInvocation.call_id)
            )
            return result.scalar_one_or_none() is not None

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

## Current tests/test_trip_form_tool_flow.py
```
import json
import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.dependencies import get_current_user
from app.governance.tool_invocations import CompletionOutcome, ToolInvocationRecord
from app.main import app
from app.schemas.planning import TravelRequirement
from app.schemas.tools import ToolResultRequest


class InMemoryInvocationRepository:
    def __init__(self, records):
        self.records = {record.call_id: record.model_copy(deep=True) for record in records}
        self.lock = asyncio.Lock()

    async def get_for_user(self, call_id, user_id):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        return record.model_copy(deep=True)

    async def update_partial(self, call_id, user_id, partial_values):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        record.partial_values = {**record.partial_values, **partial_values}
        record.version += 1
        return record.model_copy(deep=True)

    async def complete_once(self, call_id, user_id, result):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        if record.status == "completed":
            return CompletionOutcome(record=record.model_copy(deep=True), completed_now=False)
        record.status = "completed"
        record.result = result
        record.version += 1
        return CompletionOutcome(record=record.model_copy(deep=True), completed_now=True)

    async def claim_processing(self, call_id, user_id, lease_timeout):
        async with self.lock:
            record = self.records.get(call_id)
            if record is None or record.user_id != user_id:
                return None
            stale = (
                record.status == "processing"
                and datetime.now(timezone.utc) - record.updated_at >= lease_timeout
            )
            if record.status not in {"pending", "processing"} or (
                record.status == "processing" and not stale
            ):
                from app.governance.tool_invocations import ProcessingOutcome

                return ProcessingOutcome(
                    record=record.model_copy(deep=True),
                    claimed=False,
                    claim_version=record.version,
                )
            record.status = "processing"
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            from app.governance.tool_invocations import ProcessingOutcome

            return ProcessingOutcome(
                record=record.model_copy(deep=True),
                claimed=True,
                claim_version=record.version,
            )

    async def finish_processing(self, call_id, user_id, expected_version, durable_result, session=None):
        async with self.lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "completed"
            record.result = durable_result
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def release_processing(self, call_id, user_id, expected_version):
        async with self.lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "pending"
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def renew_processing(self, call_id, user_id, expected_version):
        async with self.lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return False
            record.updated_at = datetime.now(timezone.utc)
            return True


class RecordingSession:
    def __init__(self):
        self.messages = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def add(self, message):
        self.messages.append(message)

    async def commit(self):
        return None

    async def refresh(self, _message):
        return None

    def begin(self):
        return self


class RecordingEventRepository:
    def __init__(self):
        self.events = []

    async def append(self, event):
        event.sequence = len(self.events) + 1
        self.events.append(event)
        return event


class FakeDraft:
    def __init__(self, destination):
        self.destination = destination

    def model_dump(self, *, mode):
        assert mode == "json"
        return {"destination": self.destination, "summary": "confirmed itinerary"}


def invocation(*, user_id, status="pending", tool="collect_trip_requirements"):
    return ToolInvocationRecord(
        call_id="call-1",
        user_id=user_id,
        conversation_id=str(uuid4()),
        tool=tool,
        status=status,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def parse_sse(response):
    return [
        json.loads(frame.removeprefix("data: ").strip())
        for frame in response.text.split("\n\n")
        if frame.strip()
    ]


@asynccontextmanager
async def endpoint_client(user):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def configure_endpoint(monkeypatch, repository):
    from app.api.v1 import tools

    session = RecordingSession()
    events = RecordingEventRepository()
    monkeypatch.setattr(tools, "PostgresToolInvocationRepository", lambda: repository, raising=False)
    monkeypatch.setattr(tools, "PostgresEventRepository", lambda: events, raising=False)
    monkeypatch.setattr(tools, "async_session_maker", lambda: session, raising=False)

    async def checkpointer():
        return None

    monkeypatch.setattr(tools, "get_checkpointer", checkpointer, raising=False)
    return session, events


def test_trip_form_result_route_is_registered():
    paths = {route.path for route in app.routes}

    assert "/api/v1/chat/tools/{call_id}/result" in paths


@pytest.mark.asyncio
async def test_tool_result_rejects_another_users_call(monkeypatch):
    owner_id = str(uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=owner_id)])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(SimpleNamespace(id=uuid4())) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": {
                "destination": "Kyoto", "departure_date": "2026-08-03", "days": 4,
            }},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        None,
        {"destination": "Kyoto", "days": 4},
        {"destination": "Kyoto", "departure_date": "invalid", "days": 4},
        {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 0},
        {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4, "extra": True},
    ],
)
async def test_completed_result_requires_valid_trip_fields(monkeypatch, result):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": result},
        )

    assert response.status_code == 422
    assert repository.records["call-1"].status == "pending"


@pytest.mark.asyncio
async def test_tool_result_rejects_wrong_stored_tool(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository(
        [invocation(user_id=str(user.id), tool="other_tool")]
    )
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "recommend_destination"},
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_recommendation_requires_a_pending_call(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id), status="cancelled")])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "recommend_destination"},
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_recommendation_keeps_call_pending_and_returns_rag_answer(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    session, _ = configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    queries = []

    async def recommend(query):
        queries.append(query)
        return "Kyoto works well for a four-day food-focused trip."

    monkeypatch.setattr(tools, "answer_open_question", recommend, raising=False)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={
                "tool": "collect_trip_requirements",
                "status": "recommend_destination",
                "partial_values": {"days": 4, "interests": "food"},
            },
        )

    events = parse_sse(response)
    assert response.status_code == 200
    assert [event["type"] for event in events] == ["token", "tool_result", "done"]
    assert events[0]["content"] == "Kyoto works well for a four-day food-focused trip."
    assert events[1]["status"] == "awaiting_destination"
    assert repository.records["call-1"].status == "pending"
    assert repository.records["call-1"].partial_values == {"days": 4, "interests": "food"}
    assert queries and "food" in queries[0]
    assert session.messages[-1].extra_info["tool_result"]["status"] == "awaiting_destination"


@pytest.mark.asyncio
async def test_completed_result_invokes_supervisor_once_with_confirmed_fields(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    session, events = configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    calls = []

    async def supervisor(requirement, **kwargs):
        calls.append((requirement, kwargs))
        await kwargs["event_service"].emit(
            task_id=kwargs["task_id"],
            user_id=kwargs["user_id"],
            conversation_id=kwargs["conversation_id"],
            event_type="task_completed",
        )
        return FakeDraft(requirement.destination)

    monkeypatch.setattr(tools, "run_travel_planning", supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        response = await client.post("/api/v1/chat/tools/call-1/result", json=payload)
        duplicate_response = await client.post(
            "/api/v1/chat/tools/call-1/result", json=payload
        )

    stream = parse_sse(response)
    duplicate_stream = parse_sse(duplicate_response)
    assert [event["type"] for event in stream] == ["result", "token", "done"]
    assert len(calls) == 1
    requirement, kwargs = calls[0]
    assert isinstance(requirement, TravelRequirement)
    assert requirement.destination == "Kyoto"
    assert requirement.departure_date == date(2026, 8, 3)
    assert requirement.days == 4
    assert kwargs["task_id"] == "call-1"
    durable = repository.records["call-1"].result
    assert durable["confirmed_result"] == payload["result"]
    assert durable["task_id"] == "call-1"
    assert [
        (event["type"], event.get("payload")) for event in duplicate_stream
    ] == [
        (event["type"], event.get("payload")) for event in stream
    ]
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]
    assert events.events[-1].event_type == "task_completed"
    assert session.messages[-1].extra_info["tool_result"]["status"] == "completed"
    assert session.messages[-1].extra_info["assistant_result"] == stream[0]["payload"]["result"]


@pytest.mark.asyncio
async def test_duplicate_completed_result_uses_stored_completion_without_supervisor(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    confirmed = {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4}
    durable = {
        "confirmed_result": confirmed,
        "task_id": "call-1",
        "assistant_result": {"destination": "Kyoto", "summary": "confirmed itinerary"},
        "assistant_markdown": '{"destination": "Kyoto", "summary": "confirmed itinerary"}',
    }
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id), status="completed")])
    repository.records["call-1"].result = durable
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    async def unexpected_supervisor(*_args, **_kwargs):
        raise AssertionError("duplicate completion must not invoke Supervisor")

    monkeypatch.setattr(tools, "run_travel_planning", unexpected_supervisor, raising=False)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": confirmed},
        )

    stream = parse_sse(response)
    assert response.status_code == 200
    assert stream[0]["type"] == "result"
    assert stream[0]["payload"]["result"] == durable["assistant_result"]


@pytest.mark.asyncio
async def test_supervisor_failure_releases_claim_and_sanitizes_retryable_error(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    async def failing_supervisor(*_args, **_kwargs):
        raise RuntimeError("database password leaked")

    monkeypatch.setattr(tools, "run_travel_planning", failing_supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        response = await client.post("/api/v1/chat/tools/call-1/result", json=payload)

    events = parse_sse(response)
    assert [event["type"] for event in events] == ["error", "done"]
    assert events[0]["payload"]["retryable"] is True
    assert "database password leaked" not in json.dumps(events[0])
    assert repository.records["call-1"].status == "pending"

    async def successful_supervisor(*_args, **_kwargs):
        return FakeDraft("Kyoto")

    monkeypatch.setattr(tools, "run_travel_planning", successful_supervisor, raising=False)

    async with endpoint_client(user) as client:
        retry = await client.post("/api/v1/chat/tools/call-1/result", json=payload)

    assert parse_sse(retry)[0]["type"] == "result"
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]


@pytest.mark.asyncio
async def test_active_processing_duplicate_returns_nonterminal_tool_result(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    active = invocation(user_id=str(user.id), status="processing")
    active.updated_at = datetime.now(timezone.utc)
    repository = InMemoryInvocationRepository([active])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    async def unexpected_supervisor(*_args, **_kwargs):
        raise AssertionError("active processing duplicate must not invoke Supervisor")

    monkeypatch.setattr(tools, "run_travel_planning", unexpected_supervisor, raising=False)
    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={
                "tool": "collect_trip_requirements",
                "status": "completed",
                "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
            },
        )

    events = parse_sse(response)
    assert [event["type"] for event in events] == ["tool_result", "done"]
    assert events[0]["status"] == "processing"
    assert events[0]["payload"]["terminal"] is False


@pytest.mark.asyncio
async def test_heartbeat_prevents_stale_reclaim_during_long_supervisor(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    monkeypatch.setattr(tools, "PROCESSING_LEASE_TIMEOUT", timedelta(milliseconds=45))
    supervisor_started = asyncio.Event()
    supervisor_calls = []

    async def slow_supervisor(requirement, **_kwargs):
        supervisor_calls.append(requirement.destination)
        supervisor_started.set()
        await asyncio.sleep(0.12)
        return FakeDraft(requirement.destination)

    monkeypatch.setattr(tools, "run_travel_planning", slow_supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        first_request = asyncio.create_task(
            client.post("/api/v1/chat/tools/call-1/result", json=payload)
        )
        await asyncio.wait_for(supervisor_started.wait(), timeout=1)
        await asyncio.sleep(0.07)
        duplicate = await client.post("/api/v1/chat/tools/call-1/result", json=payload)
        first = await first_request

    duplicate_events = parse_sse(duplicate)
    assert [event["type"] for event in duplicate_events] == ["tool_result", "done"]
    assert duplicate_events[0]["status"] == "processing"
    assert supervisor_calls == ["Kyoto"]


@pytest.mark.asyncio
async def test_heartbeat_lease_loss_prevents_finish_and_returns_retryable_conflict(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    monkeypatch.setattr(tools, "PROCESSING_LEASE_TIMEOUT", timedelta(milliseconds=30))

    async def lease_lost(*_args, **_kwargs):
        return False

    async def slow_supervisor(*_args, **_kwargs):
        await asyncio.sleep(0.04)
        return FakeDraft("Kyoto")

    monkeypatch.setattr(repository, "renew_processing", lease_lost)
    monkeypatch.setattr(tools, "run_travel_planning", slow_supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        response = await client.post("/api/v1/chat/tools/call-1/result", json=payload)

    event = parse_sse(response)[0]
    assert event["type"] == "error"
    assert event["payload"]["code"] == "processing_conflict"
    assert event["payload"]["retryable"] is True
    assert repository.records["call-1"].status == "pending"
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]


@pytest.mark.asyncio
async def test_client_cancellation_releases_claim_and_stops_heartbeat(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    session, _ = configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    started = asyncio.Event()

    async def never_finishes(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(tools, "run_travel_planning", never_finishes, raising=False)
    data = ToolResultRequest.model_validate(
        {
            "tool": "collect_trip_requirements",
            "status": "completed",
            "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
        }
    )
    record = repository.records["call-1"].model_copy(deep=True)

    async def consume():
        async for _frame in tools.tool_result_stream("call-1", data, str(user.id), record):
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=1)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert repository.records["call-1"].status == "pending"
    assert session.messages == []
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]
```

## Current tests/test_tool_invocations.py
```
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.governance.tool_invocations import (
    InMemoryToolInvocationRepository,
    PostgresToolInvocationRepository,
    ToolInvocationRecord,
)
from app.models.base import Base
import app.models  # noqa: F401


class FakeExecutionResult:
    def __init__(self, entity):
        self.entity = entity

    def scalar_one_or_none(self):
        return self.entity


class FakeSession:
    def __init__(self, *, scalar_results=(), update_entity=None):
        self.scalar_results = list(scalar_results)
        self.update_entity = update_entity
        self.added = []
        self.scalar_statements = []
        self.executed_statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return self

    async def scalar(self, statement):
        self.scalar_statements.append(statement)
        return self.scalar_results.pop(0)

    async def execute(self, statement):
        self.executed_statements.append(statement)
        return FakeExecutionResult(self.update_entity)

    def add(self, entity):
        self.added.append(entity)


class FakeSessionFactory:
    def __init__(self, *sessions):
        self.sessions = list(sessions)

    def __call__(self):
        return self.sessions.pop(0)


def postgres_entity(*, user_id, conversation_id, result, version=2, status="completed"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        call_id="c1",
        user_id=user_id,
        conversation_id=conversation_id,
        tool="collect_trip_requirements",
        status=status,
        arguments={"initial_values": {}},
        partial_values={},
        result=result,
        version=version,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_tool_result_is_idempotent():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
        )
    )

    first = await repository.complete_once(
        "c1",
        "u1",
        {"destination": "Chengdu", "departure_date": "2026-08-10", "days": 4},
    )
    second = await repository.complete_once("c1", "u1", first.record.result)

    assert first.completed_now is True
    assert first.record.status == "completed"
    assert second.completed_now is False
    assert second.record.version == first.record.version


@pytest.mark.asyncio
async def test_duplicate_completion_keeps_the_first_result():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
        )
    )

    first = await repository.complete_once("c1", "u1", {"destination": "Chengdu"})
    duplicate = await repository.complete_once("c1", "u1", {"destination": "Beijing"})

    assert duplicate.completed_now is False
    assert duplicate.record.result == first.record.result == {"destination": "Chengdu"}


@pytest.mark.asyncio
async def test_repository_records_are_isolated_from_caller_mutation():
    repository = InMemoryToolInvocationRepository()
    record = ToolInvocationRecord(
        call_id="c1",
        user_id="u1",
        conversation_id="v1",
        tool="collect_trip_requirements",
        arguments={"initial_values": {"destination": "Chengdu"}},
    )
    await repository.create(record)
    record.arguments["initial_values"]["destination"] = "Beijing"

    stored = await repository.get_for_user("c1", "u1")
    stored.arguments["initial_values"]["destination"] = "Shanghai"

    completed = await repository.complete_once("c1", "u1", {"days": [4]})
    completed.record.result["days"].append(5)
    reloaded = await repository.get_for_user("c1", "u1")

    assert reloaded.arguments == {"initial_values": {"destination": "Chengdu"}}
    assert reloaded.result == {"days": [4]}


@pytest.mark.asyncio
async def test_tool_call_is_user_scoped():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
        )
    )

    assert await repository.get_for_user("c1", "u2") is None


@pytest.mark.asyncio
async def test_partial_values_are_merged_for_the_owner_only():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
            partial_values={"destination": "Chengdu"},
        )
    )

    updated = await repository.update_partial("c1", "u1", {"days": 4})

    assert updated is not None
    assert updated.partial_values == {"destination": "Chengdu", "days": 4}
    assert await repository.update_partial("c1", "u2", {"days": 5}) is None


@pytest.mark.asyncio
async def test_partial_values_do_not_update_a_non_pending_call():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
            status="processing",
        )
    )

    assert await repository.update_partial("c1", "u1", {"days": 4}) is None
    stored = await repository.get_for_user("c1", "u1")
    assert stored.partial_values == {}


@pytest.mark.asyncio
async def test_processing_claim_has_one_concurrent_winner():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements"
        )
    )

    outcomes = await asyncio.gather(
        repository.claim_processing("c1", "u1", timedelta(seconds=30)),
        repository.claim_processing("c1", "u1", timedelta(seconds=30)),
    )

    assert sum(outcome.claimed for outcome in outcomes) == 1
    assert all(outcome.record.status == "processing" for outcome in outcomes)
    winner = next(outcome for outcome in outcomes if outcome.claimed)
    loser = next(outcome for outcome in outcomes if not outcome.claimed)
    assert winner.claim_version == loser.claim_version


@pytest.mark.asyncio
async def test_stale_processing_claim_can_be_reclaimed():
    repository = InMemoryToolInvocationRepository()
    record = ToolInvocationRecord(
        call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements",
        status="processing", version=4,
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    await repository.create(record)

    outcome = await repository.claim_processing("c1", "u1", timedelta(seconds=30))

    assert outcome.claimed is True
    assert outcome.claim_version == 5
    assert outcome.record.status == "processing"


@pytest.mark.asyncio
async def test_processing_finish_and_release_require_matching_claim_version():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements"
        )
    )
    claim = await repository.claim_processing("c1", "u1", timedelta(seconds=30))

    assert await repository.finish_processing("c1", "u1", claim.claim_version - 1, {}) is None
    released = await repository.release_processing("c1", "u1", claim.claim_version)
    assert released.status == "pending"
    assert await repository.finish_processing("c1", "u1", claim.claim_version, {}) is None


@pytest.mark.asyncio
async def test_processing_renewal_requires_matching_version_and_status():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements"
        )
    )
    claim = await repository.claim_processing("c1", "u1", timedelta(seconds=30))

    assert await repository.renew_processing("c1", "u1", claim.claim_version) is True
    assert await repository.renew_processing("c1", "u1", claim.claim_version - 1) is False
    await repository.release_processing("c1", "u1", claim.claim_version)
    assert await repository.renew_processing("c1", "u1", claim.claim_version) is False


def test_tool_invocation_model_is_registered():
    assert "tool_invocation" in Base.metadata.tables


@pytest.mark.asyncio
async def test_postgres_create_rejects_conversation_not_owned_by_user():
    session = FakeSession(scalar_results=[None])
    repository = PostgresToolInvocationRepository(FakeSessionFactory(session))
    record = ToolInvocationRecord(
        call_id="c1",
        user_id=str(uuid4()),
        conversation_id=str(uuid4()),
        tool="collect_trip_requirements",
    )

    with pytest.raises(PermissionError):
        await repository.create(record)

    assert session.added == []
    assert len(session.scalar_statements) == 1


@pytest.mark.asyncio
async def test_postgres_create_in_session_uses_the_callers_transaction_and_checks_ownership():
    user_id = uuid4()
    conversation_id = uuid4()
    session = FakeSession(scalar_results=[conversation_id])
    repository = PostgresToolInvocationRepository()
    record = ToolInvocationRecord(
        call_id="c1",
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        tool="collect_trip_requirements",
        arguments={"initial_values": {"destination": "Chengdu"}},
    )

    created = await repository.create_in_session(session, record)

    assert created == record
    assert len(session.scalar_statements) == 1
    assert len(session.added) == 1
    assert session.added[0].call_id == "c1"
    assert session.added[0].user_id == user_id
    assert session.added[0].conversation_id == conversation_id


@pytest.mark.asyncio
async def test_postgres_completion_marks_only_returned_update_row_as_newly_completed():
    user_id = uuid4()
    conversation_id = uuid4()
    result = {"destination": "Chengdu"}
    claimed_session = FakeSession(
        update_entity=postgres_entity(
            user_id=user_id,
            conversation_id=conversation_id,
            result=result,
        )
    )
    duplicate_session = FakeSession(
        scalar_results=[
            postgres_entity(
                user_id=user_id,
                conversation_id=conversation_id,
                result=result,
            )
        ]
    )
    repository = PostgresToolInvocationRepository(
        FakeSessionFactory(claimed_session, duplicate_session)
    )

    claimed = await repository.complete_once("c1", str(user_id), result)
    duplicate = await repository.complete_once("c1", str(user_id), {"destination": "Beijing"})

    assert claimed.completed_now is True
    assert duplicate.completed_now is False
    assert duplicate.record.result == result
    assert len(claimed_session.executed_statements) == 1
    assert len(duplicate_session.executed_statements) == 1
    completion_sql = str(
        claimed_session.executed_statements[0].compile(dialect=postgresql.dialect())
    )
    assert "tool_invocation.call_id =" in completion_sql
    assert "tool_invocation.user_id =" in completion_sql
    assert "tool_invocation.status !=" in completion_sql


@pytest.mark.asyncio
async def test_postgres_processing_claim_uses_pending_or_stale_lease_predicate():
    user_id = uuid4()
    conversation_id = uuid4()
    session = FakeSession(
        update_entity=postgres_entity(
            user_id=user_id,
            conversation_id=conversation_id,
            result=None,
            version=3,
            status="processing",
        )
    )
    repository = PostgresToolInvocationRepository(FakeSessionFactory(session))

    outcome = await repository.claim_processing("c1", str(user_id), timedelta(seconds=30))

    assert outcome.claimed is True
    assert outcome.claim_version == 3
    claim_sql = str(session.executed_statements[0].compile(dialect=postgresql.dialect()))
    assert "tool_invocation.status" in claim_sql
    assert "tool_invocation.updated_at <=" in claim_sql
    assert "tool_invocation.version +" in claim_sql


@pytest.mark.asyncio
async def test_postgres_finish_and_release_require_processing_version_match():
    user_id = uuid4()
    conversation_id = uuid4()
    finish_session = FakeSession(
        update_entity=postgres_entity(
            user_id=user_id,
            conversation_id=conversation_id,
            result={"task_id": "c1"},
            version=5,
            status="completed",
        )
    )
    release_session = FakeSession(
        update_entity=postgres_entity(
            user_id=user_id,
            conversation_id=conversation_id,
            result=None,
            version=5,
            status="pending",
        )
    )
    repository = PostgresToolInvocationRepository(
        FakeSessionFactory(finish_session, release_session)
    )

    finished = await repository.finish_processing("c1", str(user_id), 4, {"task_id": "c1"})
    released = await repository.release_processing("c1", str(user_id), 4)

    assert finished.status == "completed"
    assert released.status == "pending"
    finish_sql = str(finish_session.executed_statements[0].compile(dialect=postgresql.dialect()))
    release_sql = str(release_session.executed_statements[0].compile(dialect=postgresql.dialect()))
    assert "tool_invocation.version =" in finish_sql
    assert "tool_invocation.status =" in finish_sql
    assert "tool_invocation.version =" in release_sql


@pytest.mark.asyncio
async def test_postgres_processing_renewal_requires_processing_version_match():
    user_id = uuid4()
    conversation_id = uuid4()
    session = FakeSession(
        update_entity=postgres_entity(
            user_id=user_id,
            conversation_id=conversation_id,
            result=None,
            version=5,
            status="processing",
        )
    )
    repository = PostgresToolInvocationRepository(FakeSessionFactory(session))

    renewed = await repository.renew_processing("c1", str(user_id), 5)

    assert renewed is True
    renewal_sql = str(session.executed_statements[0].compile(dialect=postgresql.dialect()))
    assert "tool_invocation.status =" in renewal_sql
    assert "tool_invocation.version =" in renewal_sql
    assert "updated_at" in renewal_sql
```

# Lease-Loss Cancellation Addendum

## Current app/api/v1/tools.py
```
"""Tool-result SSE API."""

import asyncio
import json
from contextlib import suppress
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.agents.supervisor import run_travel_planning
from app.api.dependencies import get_current_user
from app.core.checkpointer import get_checkpointer
from app.governance.events import TaskEventService
from app.governance.postgres import PostgresEventRepository
from app.governance.tool_invocations import PostgresToolInvocationRepository
from app.models.base import async_session_maker
from app.models.message import Message
from app.models.user import User
from app.schemas.events import SSEEvent
from app.schemas.planning import TravelRequirement
from app.schemas.tools import ToolResultRequest
from app.services.open_qa import answer_open_question
from app.services.planning import render_plan_markdown


router = APIRouter(prefix="/chat/tools", tags=["chat tools"])
PROCESSING_LEASE_TIMEOUT = timedelta(minutes=2)


class ProcessingLeaseLostError(RuntimeError):
    pass


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def save_assistant_message(conversation_id: str, content: str, extra_info: dict) -> None:
    async with async_session_maker() as db:
        message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            extra_info=extra_info,
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)


def recommendation_query(partial_values: dict) -> str:
    values = json.dumps(partial_values, ensure_ascii=False, sort_keys=True)
    return f"Recommend a travel destination based on these confirmed preferences: {values}"


async def processing_heartbeat(
    repository,
    call_id: str,
    user_id: str,
    claim_version: int,
    lease_timeout: timedelta,
    lease_lost: asyncio.Event,
) -> None:
    interval = max(lease_timeout.total_seconds() / 3, 0.01)
    try:
        while True:
            await asyncio.sleep(interval)
            if not await repository.renew_processing(call_id, user_id, claim_version):
                lease_lost.set()
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        lease_lost.set()


async def stop_processing_heartbeat(heartbeat_task: asyncio.Task | None) -> None:
    if heartbeat_task is None:
        return
    heartbeat_task.cancel()
    with suppress(asyncio.CancelledError):
        await heartbeat_task


async def cancel_and_await_task(task: asyncio.Task | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task


async def existing_completion_stream(call_id: str, conversation_id: str, result: dict | None):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    durable = result if isinstance(result, dict) else {}
    task_id = durable.get("task_id", call_id)
    assistant_result = durable.get("assistant_result", result)
    assistant_markdown = durable.get(
        "assistant_markdown", "A travel-planning task has already been submitted."
    )
    yield sse(
        event(
            "result",
            {"task_id": task_id, "status": "completed", "result": assistant_result},
        )
    )
    yield sse(event("token", {"content": assistant_markdown}))
    yield sse(event("done"))


async def processing_stream(call_id: str, conversation_id: str):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    yield sse(
        event(
            "tool_result",
            {
                "tool": "collect_trip_requirements",
                "status": "processing",
                "terminal": False,
            },
        )
    )
    yield sse(event("done"))


async def tool_result_stream(call_id: str, data: ToolResultRequest, user_id: str, record):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=record.conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    claim_version = None
    repository = None
    heartbeat_task = None
    planning_task = None
    try:
        repository = PostgresToolInvocationRepository()

        if data.status == "recommend_destination":
            if record.status == "processing":
                async for frame in processing_stream(call_id, record.conversation_id):
                    yield frame
                return
            updated = await repository.update_partial(call_id, user_id, data.partial_values)
            if updated is None:
                raise ValueError("Tool invocation is unavailable")

            answer = await answer_open_question(recommendation_query(updated.partial_values))
            tool_result = {
                "tool": data.tool,
                "status": "awaiting_destination",
                "partial_values": updated.partial_values,
            }
            await save_assistant_message(
                record.conversation_id,
                answer,
                {"tool_result": tool_result},
            )
            yield sse(event("token", {"content": answer}))
            yield sse(event("tool_result", tool_result))
            yield sse(event("done"))
            return

        if data.status != "completed" or data.result is None:
            raise ValueError("A completed tool result requires destination, departure_date, and days")

        confirmed_result = data.result.model_dump(mode="json")
        claim = await repository.claim_processing(
            call_id, user_id, PROCESSING_LEASE_TIMEOUT
        )
        if claim is None:
            raise ValueError("Tool invocation is unavailable")
        if not claim.claimed:
            if claim.record.status == "completed":
                async for frame in existing_completion_stream(
                    call_id, claim.record.conversation_id, claim.record.result
                ):
                    yield frame
            elif claim.record.status == "processing":
                async for frame in processing_stream(call_id, claim.record.conversation_id):
                    yield frame
            else:
                raise ValueError("Tool invocation is not available for processing")
            return

        claim_version = claim.claim_version
        record = claim.record
        requirement = TravelRequirement(**data.result.model_dump())
        event_service = TaskEventService(PostgresEventRepository())
        lease_lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            processing_heartbeat(
                repository,
                call_id,
                user_id,
                claim_version,
                PROCESSING_LEASE_TIMEOUT,
                lease_lost,
            ),
            name=f"tool-result-heartbeat:{call_id}",
        )
        planning_task = asyncio.create_task(
            run_travel_planning(
                requirement,
                checkpointer=await get_checkpointer(),
                event_service=event_service,
                task_id=call_id,
                user_id=user_id,
                conversation_id=record.conversation_id,
            ),
            name=f"tool-result-planning:{call_id}",
        )
        try:
            done, _pending = await asyncio.wait(
                {planning_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done and lease_lost.is_set():
                await cancel_and_await_task(planning_task)
                planning_task = None
                await stop_processing_heartbeat(heartbeat_task)
                heartbeat_task = None
                claim_version = None
                raise ProcessingLeaseLostError("processing lease lost")
            draft = planning_task.result()
        finally:
            if planning_task is not None and planning_task.done():
                planning_task = None
            await stop_processing_heartbeat(heartbeat_task)
            heartbeat_task = None
        if lease_lost.is_set():
            raise ProcessingLeaseLostError("processing lease lost")
        assistant_result = draft.model_dump(mode="json")
        assistant_content = json.dumps(assistant_result, ensure_ascii=False)
        if hasattr(draft, "itinerary") and hasattr(draft, "requirement"):
            assistant_content = render_plan_markdown(draft)
        durable_result = {
            "confirmed_result": confirmed_result,
            "task_id": call_id,
            "assistant_result": assistant_result,
            "assistant_markdown": assistant_content,
            "draft": assistant_result,
            "route": getattr(draft, "route", None),
        }
        tool_result = {
            "tool": data.tool,
            "status": "completed",
            "result": confirmed_result,
            "task_id": call_id,
        }
        async with async_session_maker() as db:
            async with db.begin():
                finished = await repository.finish_processing(
                    call_id,
                    user_id,
                    claim_version,
                    durable_result,
                    session=db,
                )
                if finished is None:
                    raise ValueError("Tool invocation processing claim was lost")
                db.add(
                    Message(
                        conversation_id=finished.conversation_id,
                        role="assistant",
                        content=assistant_content,
                        extra_info={
                            "tool_result": tool_result,
                            "assistant_result": assistant_result,
                        },
                    )
                )
        claim_version = None
        yield sse(event("result", {"task_id": call_id, "status": "completed", "result": assistant_result}))
        yield sse(event("token", {"content": assistant_content}))
        yield sse(event("done"))
    except asyncio.CancelledError:
        await cancel_and_await_task(planning_task)
        await stop_processing_heartbeat(heartbeat_task)
        if repository is not None and claim_version is not None:
            with suppress(Exception):
                await repository.release_processing(call_id, user_id, claim_version)
        raise
    except Exception as exc:
        await cancel_and_await_task(planning_task)
        await stop_processing_heartbeat(heartbeat_task)
        if repository is not None and claim_version is not None:
            try:
                await repository.release_processing(call_id, user_id, claim_version)
            except Exception:
                pass
        yield sse(
            event(
                "error",
                {
                    "code": (
                        "processing_conflict"
                        if isinstance(exc, ProcessingLeaseLostError)
                        else "internal_error"
                    ),
                    "message": "Travel planning is temporarily unavailable. Please retry.",
                    "retryable": True,
                },
            )
        )
        yield sse(event("done"))


@router.post("/{call_id}/result")
async def submit_tool_result(
    call_id: str,
    data: ToolResultRequest,
    user: User = Depends(get_current_user),
):
    user_id = str(user.id)
    repository = PostgresToolInvocationRepository()
    record = await repository.get_for_user(call_id, user_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool invocation not found")
    if record.tool != data.tool:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tool does not match invocation")
    if data.status == "completed" and data.result is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Completed results require destination, departure_date, and days",
        )
    if data.status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cancelled tool results are not supported",
        )
    if record.status == "completed" and data.status == "completed":
        return StreamingResponse(
            existing_completion_stream(call_id, record.conversation_id, record.result),
            media_type="text/event-stream",
        )
    if record.status not in {"pending", "processing"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tool invocation is not pending")

    return StreamingResponse(
        tool_result_stream(call_id, data, user_id, record),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

## Current tests/test_trip_form_tool_flow.py
```
import json
import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.dependencies import get_current_user
from app.governance.tool_invocations import CompletionOutcome, ToolInvocationRecord
from app.main import app
from app.schemas.planning import TravelRequirement
from app.schemas.tools import ToolResultRequest


class InMemoryInvocationRepository:
    def __init__(self, records):
        self.records = {record.call_id: record.model_copy(deep=True) for record in records}
        self.lock = asyncio.Lock()

    async def get_for_user(self, call_id, user_id):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        return record.model_copy(deep=True)

    async def update_partial(self, call_id, user_id, partial_values):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        record.partial_values = {**record.partial_values, **partial_values}
        record.version += 1
        return record.model_copy(deep=True)

    async def complete_once(self, call_id, user_id, result):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        if record.status == "completed":
            return CompletionOutcome(record=record.model_copy(deep=True), completed_now=False)
        record.status = "completed"
        record.result = result
        record.version += 1
        return CompletionOutcome(record=record.model_copy(deep=True), completed_now=True)

    async def claim_processing(self, call_id, user_id, lease_timeout):
        async with self.lock:
            record = self.records.get(call_id)
            if record is None or record.user_id != user_id:
                return None
            stale = (
                record.status == "processing"
                and datetime.now(timezone.utc) - record.updated_at >= lease_timeout
            )
            if record.status not in {"pending", "processing"} or (
                record.status == "processing" and not stale
            ):
                from app.governance.tool_invocations import ProcessingOutcome

                return ProcessingOutcome(
                    record=record.model_copy(deep=True),
                    claimed=False,
                    claim_version=record.version,
                )
            record.status = "processing"
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            from app.governance.tool_invocations import ProcessingOutcome

            return ProcessingOutcome(
                record=record.model_copy(deep=True),
                claimed=True,
                claim_version=record.version,
            )

    async def finish_processing(self, call_id, user_id, expected_version, durable_result, session=None):
        async with self.lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "completed"
            record.result = durable_result
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def release_processing(self, call_id, user_id, expected_version):
        async with self.lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "pending"
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def renew_processing(self, call_id, user_id, expected_version):
        async with self.lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return False
            record.updated_at = datetime.now(timezone.utc)
            return True


class RecordingSession:
    def __init__(self):
        self.messages = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def add(self, message):
        self.messages.append(message)

    async def commit(self):
        return None

    async def refresh(self, _message):
        return None

    def begin(self):
        return self


class RecordingEventRepository:
    def __init__(self):
        self.events = []

    async def append(self, event):
        event.sequence = len(self.events) + 1
        self.events.append(event)
        return event


class FakeDraft:
    def __init__(self, destination):
        self.destination = destination

    def model_dump(self, *, mode):
        assert mode == "json"
        return {"destination": self.destination, "summary": "confirmed itinerary"}


def invocation(*, user_id, status="pending", tool="collect_trip_requirements"):
    return ToolInvocationRecord(
        call_id="call-1",
        user_id=user_id,
        conversation_id=str(uuid4()),
        tool=tool,
        status=status,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def parse_sse(response):
    return [
        json.loads(frame.removeprefix("data: ").strip())
        for frame in response.text.split("\n\n")
        if frame.strip()
    ]


@asynccontextmanager
async def endpoint_client(user):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def configure_endpoint(monkeypatch, repository):
    from app.api.v1 import tools

    session = RecordingSession()
    events = RecordingEventRepository()
    monkeypatch.setattr(tools, "PostgresToolInvocationRepository", lambda: repository, raising=False)
    monkeypatch.setattr(tools, "PostgresEventRepository", lambda: events, raising=False)
    monkeypatch.setattr(tools, "async_session_maker", lambda: session, raising=False)

    async def checkpointer():
        return None

    monkeypatch.setattr(tools, "get_checkpointer", checkpointer, raising=False)
    return session, events


def test_trip_form_result_route_is_registered():
    paths = {route.path for route in app.routes}

    assert "/api/v1/chat/tools/{call_id}/result" in paths


@pytest.mark.asyncio
async def test_tool_result_rejects_another_users_call(monkeypatch):
    owner_id = str(uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=owner_id)])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(SimpleNamespace(id=uuid4())) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": {
                "destination": "Kyoto", "departure_date": "2026-08-03", "days": 4,
            }},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        None,
        {"destination": "Kyoto", "days": 4},
        {"destination": "Kyoto", "departure_date": "invalid", "days": 4},
        {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 0},
        {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4, "extra": True},
    ],
)
async def test_completed_result_requires_valid_trip_fields(monkeypatch, result):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": result},
        )

    assert response.status_code == 422
    assert repository.records["call-1"].status == "pending"


@pytest.mark.asyncio
async def test_tool_result_rejects_wrong_stored_tool(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository(
        [invocation(user_id=str(user.id), tool="other_tool")]
    )
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "recommend_destination"},
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_recommendation_requires_a_pending_call(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id), status="cancelled")])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "recommend_destination"},
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_recommendation_keeps_call_pending_and_returns_rag_answer(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    session, _ = configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    queries = []

    async def recommend(query):
        queries.append(query)
        return "Kyoto works well for a four-day food-focused trip."

    monkeypatch.setattr(tools, "answer_open_question", recommend, raising=False)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={
                "tool": "collect_trip_requirements",
                "status": "recommend_destination",
                "partial_values": {"days": 4, "interests": "food"},
            },
        )

    events = parse_sse(response)
    assert response.status_code == 200
    assert [event["type"] for event in events] == ["token", "tool_result", "done"]
    assert events[0]["content"] == "Kyoto works well for a four-day food-focused trip."
    assert events[1]["status"] == "awaiting_destination"
    assert repository.records["call-1"].status == "pending"
    assert repository.records["call-1"].partial_values == {"days": 4, "interests": "food"}
    assert queries and "food" in queries[0]
    assert session.messages[-1].extra_info["tool_result"]["status"] == "awaiting_destination"


@pytest.mark.asyncio
async def test_completed_result_invokes_supervisor_once_with_confirmed_fields(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    session, events = configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    calls = []

    async def supervisor(requirement, **kwargs):
        calls.append((requirement, kwargs))
        await kwargs["event_service"].emit(
            task_id=kwargs["task_id"],
            user_id=kwargs["user_id"],
            conversation_id=kwargs["conversation_id"],
            event_type="task_completed",
        )
        return FakeDraft(requirement.destination)

    monkeypatch.setattr(tools, "run_travel_planning", supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        response = await client.post("/api/v1/chat/tools/call-1/result", json=payload)
        duplicate_response = await client.post(
            "/api/v1/chat/tools/call-1/result", json=payload
        )

    stream = parse_sse(response)
    duplicate_stream = parse_sse(duplicate_response)
    assert [event["type"] for event in stream] == ["result", "token", "done"]
    assert len(calls) == 1
    requirement, kwargs = calls[0]
    assert isinstance(requirement, TravelRequirement)
    assert requirement.destination == "Kyoto"
    assert requirement.departure_date == date(2026, 8, 3)
    assert requirement.days == 4
    assert kwargs["task_id"] == "call-1"
    durable = repository.records["call-1"].result
    assert durable["confirmed_result"] == payload["result"]
    assert durable["task_id"] == "call-1"
    assert [
        (event["type"], event.get("payload")) for event in duplicate_stream
    ] == [
        (event["type"], event.get("payload")) for event in stream
    ]
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]
    assert events.events[-1].event_type == "task_completed"
    assert session.messages[-1].extra_info["tool_result"]["status"] == "completed"
    assert session.messages[-1].extra_info["assistant_result"] == stream[0]["payload"]["result"]


@pytest.mark.asyncio
async def test_duplicate_completed_result_uses_stored_completion_without_supervisor(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    confirmed = {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4}
    durable = {
        "confirmed_result": confirmed,
        "task_id": "call-1",
        "assistant_result": {"destination": "Kyoto", "summary": "confirmed itinerary"},
        "assistant_markdown": '{"destination": "Kyoto", "summary": "confirmed itinerary"}',
    }
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id), status="completed")])
    repository.records["call-1"].result = durable
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    async def unexpected_supervisor(*_args, **_kwargs):
        raise AssertionError("duplicate completion must not invoke Supervisor")

    monkeypatch.setattr(tools, "run_travel_planning", unexpected_supervisor, raising=False)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": confirmed},
        )

    stream = parse_sse(response)
    assert response.status_code == 200
    assert stream[0]["type"] == "result"
    assert stream[0]["payload"]["result"] == durable["assistant_result"]


@pytest.mark.asyncio
async def test_supervisor_failure_releases_claim_and_sanitizes_retryable_error(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    async def failing_supervisor(*_args, **_kwargs):
        raise RuntimeError("database password leaked")

    monkeypatch.setattr(tools, "run_travel_planning", failing_supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        response = await client.post("/api/v1/chat/tools/call-1/result", json=payload)

    events = parse_sse(response)
    assert [event["type"] for event in events] == ["error", "done"]
    assert events[0]["payload"]["retryable"] is True
    assert "database password leaked" not in json.dumps(events[0])
    assert repository.records["call-1"].status == "pending"

    async def successful_supervisor(*_args, **_kwargs):
        return FakeDraft("Kyoto")

    monkeypatch.setattr(tools, "run_travel_planning", successful_supervisor, raising=False)

    async with endpoint_client(user) as client:
        retry = await client.post("/api/v1/chat/tools/call-1/result", json=payload)

    assert parse_sse(retry)[0]["type"] == "result"
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]


@pytest.mark.asyncio
async def test_active_processing_duplicate_returns_nonterminal_tool_result(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    active = invocation(user_id=str(user.id), status="processing")
    active.updated_at = datetime.now(timezone.utc)
    repository = InMemoryInvocationRepository([active])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    async def unexpected_supervisor(*_args, **_kwargs):
        raise AssertionError("active processing duplicate must not invoke Supervisor")

    monkeypatch.setattr(tools, "run_travel_planning", unexpected_supervisor, raising=False)
    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={
                "tool": "collect_trip_requirements",
                "status": "completed",
                "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
            },
        )

    events = parse_sse(response)
    assert [event["type"] for event in events] == ["tool_result", "done"]
    assert events[0]["status"] == "processing"
    assert events[0]["payload"]["terminal"] is False


@pytest.mark.asyncio
async def test_heartbeat_prevents_stale_reclaim_during_long_supervisor(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    monkeypatch.setattr(tools, "PROCESSING_LEASE_TIMEOUT", timedelta(milliseconds=45))
    supervisor_started = asyncio.Event()
    supervisor_calls = []

    async def slow_supervisor(requirement, **_kwargs):
        supervisor_calls.append(requirement.destination)
        supervisor_started.set()
        await asyncio.sleep(0.12)
        return FakeDraft(requirement.destination)

    monkeypatch.setattr(tools, "run_travel_planning", slow_supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        first_request = asyncio.create_task(
            client.post("/api/v1/chat/tools/call-1/result", json=payload)
        )
        await asyncio.wait_for(supervisor_started.wait(), timeout=1)
        await asyncio.sleep(0.07)
        duplicate = await client.post("/api/v1/chat/tools/call-1/result", json=payload)
        first = await first_request

    duplicate_events = parse_sse(duplicate)
    assert [event["type"] for event in duplicate_events] == ["tool_result", "done"]
    assert duplicate_events[0]["status"] == "processing"
    assert supervisor_calls == ["Kyoto"]


@pytest.mark.asyncio
async def test_lease_loss_cancels_blocking_supervisor_before_second_claim_side_effect(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    monkeypatch.setattr(tools, "PROCESSING_LEASE_TIMEOUT", timedelta(milliseconds=30))
    started = asyncio.Event()
    block = asyncio.Event()
    side_effects = []

    async def lease_lost(*_args, **_kwargs):
        return False

    monkeypatch.setattr(repository, "renew_processing", lease_lost)

    async def supervisor(requirement, **_kwargs):
        side_effects.append("first-start" if len(side_effects) == 0 else "second-start")
        if len(side_effects) == 1:
            started.set()
            try:
                await block.wait()
            except asyncio.CancelledError:
                side_effects.append("first-cancel")
                raise
        return FakeDraft(requirement.destination)

    monkeypatch.setattr(tools, "run_travel_planning", supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        first_request = asyncio.create_task(
            client.post("/api/v1/chat/tools/call-1/result", json=payload)
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.sleep(0.05)
        second_request = asyncio.create_task(
            client.post("/api/v1/chat/tools/call-1/result", json=payload)
        )
        first = await asyncio.wait_for(first_request, timeout=1)
        second = await asyncio.wait_for(second_request, timeout=1)

    assert parse_sse(first)[0]["payload"]["code"] == "processing_conflict"
    assert parse_sse(second)[0]["type"] == "result"
    assert side_effects == ["first-start", "first-cancel", "second-start"]
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]


@pytest.mark.asyncio
async def test_heartbeat_lease_loss_prevents_finish_and_returns_retryable_conflict(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    monkeypatch.setattr(tools, "PROCESSING_LEASE_TIMEOUT", timedelta(milliseconds=30))

    async def lease_lost(*_args, **_kwargs):
        return False

    async def slow_supervisor(*_args, **_kwargs):
        await asyncio.sleep(0.04)
        return FakeDraft("Kyoto")

    monkeypatch.setattr(repository, "renew_processing", lease_lost)
    monkeypatch.setattr(tools, "run_travel_planning", slow_supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        response = await client.post("/api/v1/chat/tools/call-1/result", json=payload)

    event = parse_sse(response)[0]
    assert event["type"] == "error"
    assert event["payload"]["code"] == "processing_conflict"
    assert event["payload"]["retryable"] is True
    assert repository.records["call-1"].status == "processing"
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]


@pytest.mark.asyncio
async def test_client_cancellation_releases_claim_and_stops_heartbeat(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    session, _ = configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    started = asyncio.Event()

    async def never_finishes(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(tools, "run_travel_planning", never_finishes, raising=False)
    data = ToolResultRequest.model_validate(
        {
            "tool": "collect_trip_requirements",
            "status": "completed",
            "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
        }
    )
    record = repository.records["call-1"].model_copy(deep=True)

    async def consume():
        async for _frame in tools.tool_result_stream("call-1", data, str(user.id), record):
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=1)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert repository.records["call-1"].status == "pending"
    assert session.messages == []
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]
```

# No-Reclaim Safety Addendum

## Current app/api/v1/tools.py
```
"""Tool-result SSE API."""

import asyncio
import json
from contextlib import suppress
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.agents.supervisor import run_travel_planning
from app.api.dependencies import get_current_user
from app.core.checkpointer import get_checkpointer
from app.governance.events import TaskEventService
from app.governance.postgres import PostgresEventRepository
from app.governance.tool_invocations import PostgresToolInvocationRepository
from app.models.base import async_session_maker
from app.models.message import Message
from app.models.user import User
from app.schemas.events import SSEEvent
from app.schemas.planning import TravelRequirement
from app.schemas.tools import ToolResultRequest
from app.services.open_qa import answer_open_question
from app.services.planning import render_plan_markdown


router = APIRouter(prefix="/chat/tools", tags=["chat tools"])
PROCESSING_LEASE_TIMEOUT = timedelta(minutes=2)


class ProcessingLeaseLostError(RuntimeError):
    pass


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def save_assistant_message(conversation_id: str, content: str, extra_info: dict) -> None:
    async with async_session_maker() as db:
        message = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            extra_info=extra_info,
        )
        db.add(message)
        await db.commit()
        await db.refresh(message)


def recommendation_query(partial_values: dict) -> str:
    values = json.dumps(partial_values, ensure_ascii=False, sort_keys=True)
    return f"Recommend a travel destination based on these confirmed preferences: {values}"


async def processing_heartbeat(
    repository,
    call_id: str,
    user_id: str,
    claim_version: int,
    lease_timeout: timedelta,
    lease_lost: asyncio.Event,
) -> None:
    interval = max(lease_timeout.total_seconds() / 3, 0.01)
    try:
        while True:
            await asyncio.sleep(interval)
            if not await repository.renew_processing(call_id, user_id, claim_version):
                lease_lost.set()
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        lease_lost.set()


async def stop_processing_heartbeat(heartbeat_task: asyncio.Task | None) -> None:
    if heartbeat_task is None:
        return
    heartbeat_task.cancel()
    with suppress(asyncio.CancelledError):
        await heartbeat_task


async def cancel_and_await_task(task: asyncio.Task | None) -> None:
    if task is None:
        return
    if not task.done():
        task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task


async def existing_completion_stream(call_id: str, conversation_id: str, result: dict | None):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    durable = result if isinstance(result, dict) else {}
    task_id = durable.get("task_id", call_id)
    assistant_result = durable.get("assistant_result", result)
    assistant_markdown = durable.get(
        "assistant_markdown", "A travel-planning task has already been submitted."
    )
    yield sse(
        event(
            "result",
            {"task_id": task_id, "status": "completed", "result": assistant_result},
        )
    )
    yield sse(event("token", {"content": assistant_markdown}))
    yield sse(event("done"))


async def processing_stream(call_id: str, conversation_id: str):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    yield sse(
        event(
            "tool_result",
            {
                "tool": "collect_trip_requirements",
                "status": "processing",
                "terminal": False,
            },
        )
    )
    yield sse(event("done"))


async def tool_result_stream(call_id: str, data: ToolResultRequest, user_id: str, record):
    sequence = 0

    def event(event_type: str, payload: dict | None = None) -> dict:
        nonlocal sequence
        sequence += 1
        return SSEEvent(
            type=event_type,
            task_id=call_id,
            conversation_id=record.conversation_id,
            sequence=sequence,
            payload=payload or {},
        ).legacy_payload()

    claim_version = None
    repository = None
    heartbeat_task = None
    planning_task = None
    try:
        repository = PostgresToolInvocationRepository()

        if data.status == "recommend_destination":
            if record.status == "processing":
                async for frame in processing_stream(call_id, record.conversation_id):
                    yield frame
                return
            updated = await repository.update_partial(call_id, user_id, data.partial_values)
            if updated is None:
                raise ValueError("Tool invocation is unavailable")

            answer = await answer_open_question(recommendation_query(updated.partial_values))
            tool_result = {
                "tool": data.tool,
                "status": "awaiting_destination",
                "partial_values": updated.partial_values,
            }
            await save_assistant_message(
                record.conversation_id,
                answer,
                {"tool_result": tool_result},
            )
            yield sse(event("token", {"content": answer}))
            yield sse(event("tool_result", tool_result))
            yield sse(event("done"))
            return

        if data.status != "completed" or data.result is None:
            raise ValueError("A completed tool result requires destination, departure_date, and days")

        confirmed_result = data.result.model_dump(mode="json")
        claim = await repository.claim_processing(
            call_id, user_id, PROCESSING_LEASE_TIMEOUT
        )
        if claim is None:
            raise ValueError("Tool invocation is unavailable")
        if not claim.claimed:
            if claim.record.status == "completed":
                async for frame in existing_completion_stream(
                    call_id, claim.record.conversation_id, claim.record.result
                ):
                    yield frame
            elif claim.record.status == "processing":
                async for frame in processing_stream(call_id, claim.record.conversation_id):
                    yield frame
            else:
                raise ValueError("Tool invocation is not available for processing")
            return

        claim_version = claim.claim_version
        record = claim.record
        requirement = TravelRequirement(**data.result.model_dump())
        event_service = TaskEventService(PostgresEventRepository())
        lease_lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            processing_heartbeat(
                repository,
                call_id,
                user_id,
                claim_version,
                PROCESSING_LEASE_TIMEOUT,
                lease_lost,
            ),
            name=f"tool-result-heartbeat:{call_id}",
        )
        planning_task = asyncio.create_task(
            run_travel_planning(
                requirement,
                checkpointer=await get_checkpointer(),
                event_service=event_service,
                task_id=call_id,
                user_id=user_id,
                conversation_id=record.conversation_id,
            ),
            name=f"tool-result-planning:{call_id}",
        )
        try:
            done, _pending = await asyncio.wait(
                {planning_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat_task in done and lease_lost.is_set():
                await cancel_and_await_task(planning_task)
                planning_task = None
                await stop_processing_heartbeat(heartbeat_task)
                heartbeat_task = None
                raise ProcessingLeaseLostError("processing lease lost")
            draft = planning_task.result()
        finally:
            if planning_task is not None and planning_task.done():
                planning_task = None
            await stop_processing_heartbeat(heartbeat_task)
            heartbeat_task = None
        if lease_lost.is_set():
            raise ProcessingLeaseLostError("processing lease lost")
        assistant_result = draft.model_dump(mode="json")
        assistant_content = json.dumps(assistant_result, ensure_ascii=False)
        if hasattr(draft, "itinerary") and hasattr(draft, "requirement"):
            assistant_content = render_plan_markdown(draft)
        durable_result = {
            "confirmed_result": confirmed_result,
            "task_id": call_id,
            "assistant_result": assistant_result,
            "assistant_markdown": assistant_content,
            "draft": assistant_result,
            "route": getattr(draft, "route", None),
        }
        tool_result = {
            "tool": data.tool,
            "status": "completed",
            "result": confirmed_result,
            "task_id": call_id,
        }
        async with async_session_maker() as db:
            async with db.begin():
                finished = await repository.finish_processing(
                    call_id,
                    user_id,
                    claim_version,
                    durable_result,
                    session=db,
                )
                if finished is None:
                    raise ValueError("Tool invocation processing claim was lost")
                db.add(
                    Message(
                        conversation_id=finished.conversation_id,
                        role="assistant",
                        content=assistant_content,
                        extra_info={
                            "tool_result": tool_result,
                            "assistant_result": assistant_result,
                        },
                    )
                )
        claim_version = None
        yield sse(event("result", {"task_id": call_id, "status": "completed", "result": assistant_result}))
        yield sse(event("token", {"content": assistant_content}))
        yield sse(event("done"))
    except asyncio.CancelledError:
        await cancel_and_await_task(planning_task)
        await stop_processing_heartbeat(heartbeat_task)
        if repository is not None and claim_version is not None:
            with suppress(Exception):
                await repository.release_processing(call_id, user_id, claim_version)
        raise
    except Exception as exc:
        await cancel_and_await_task(planning_task)
        await stop_processing_heartbeat(heartbeat_task)
        if repository is not None and claim_version is not None:
            try:
                await repository.release_processing(call_id, user_id, claim_version)
            except Exception:
                pass
        yield sse(
            event(
                "error",
                {
                    "code": (
                        "processing_conflict"
                        if isinstance(exc, ProcessingLeaseLostError)
                        else "internal_error"
                    ),
                    "message": "Travel planning is temporarily unavailable. Please retry.",
                    "retryable": True,
                },
            )
        )
        yield sse(event("done"))


@router.post("/{call_id}/result")
async def submit_tool_result(
    call_id: str,
    data: ToolResultRequest,
    user: User = Depends(get_current_user),
):
    user_id = str(user.id)
    repository = PostgresToolInvocationRepository()
    record = await repository.get_for_user(call_id, user_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool invocation not found")
    if record.tool != data.tool:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tool does not match invocation")
    if data.status == "completed" and data.result is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Completed results require destination, departure_date, and days",
        )
    if data.status == "cancelled":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cancelled tool results are not supported",
        )
    if record.status == "completed" and data.status == "completed":
        return StreamingResponse(
            existing_completion_stream(call_id, record.conversation_id, record.result),
            media_type="text/event-stream",
        )
    if record.status not in {"pending", "processing"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Tool invocation is not pending")

    return StreamingResponse(
        tool_result_stream(call_id, data, user_id, record),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

## Current app/governance/tool_invocations.py
```
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import async_session_maker
from app.models.conversation import Conversation
from app.models.tool_invocation import ToolInvocation


DEFAULT_PROCESSING_LEASE_TIMEOUT = timedelta(minutes=2)


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


class ProcessingOutcome(BaseModel):
    record: ToolInvocationRecord
    claimed: bool
    claim_version: int


class ToolInvocationRepository(Protocol):
    async def create(self, record: ToolInvocationRecord) -> ToolInvocationRecord: ...
    async def get_for_user(self, call_id: str, user_id: str) -> ToolInvocationRecord | None: ...
    async def update_partial(
        self, call_id: str, user_id: str, partial_values: dict[str, Any]
    ) -> ToolInvocationRecord | None: ...
    async def complete_once(
        self, call_id: str, user_id: str, result: dict[str, Any] | None
    ) -> CompletionOutcome | None: ...
    async def claim_processing(
        self,
        call_id: str,
        user_id: str,
        lease_timeout: timedelta | int | float = DEFAULT_PROCESSING_LEASE_TIMEOUT,
    ) -> ProcessingOutcome | None: ...
    async def finish_processing(
        self,
        call_id: str,
        user_id: str,
        expected_version: int,
        durable_result: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> ToolInvocationRecord | None: ...
    async def release_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> ToolInvocationRecord | None: ...
    async def renew_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> bool: ...


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
            if record is None or record.user_id != user_id or record.status != "pending":
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

    async def claim_processing(
        self,
        call_id: str,
        user_id: str,
        lease_timeout: timedelta | int | float = DEFAULT_PROCESSING_LEASE_TIMEOUT,
    ) -> ProcessingOutcome | None:
        async with self._lock:
            record = self.records.get(call_id)
            if record is None or record.user_id != user_id:
                return None
            if record.status != "pending":
                return ProcessingOutcome(
                    record=record.model_copy(deep=True),
                    claimed=False,
                    claim_version=record.version,
                )
            now = datetime.now(timezone.utc)
            record.status = "processing"
            record.version += 1
            record.updated_at = now
            return ProcessingOutcome(
                record=record.model_copy(deep=True),
                claimed=True,
                claim_version=record.version,
            )

    async def finish_processing(
        self,
        call_id: str,
        user_id: str,
        expected_version: int,
        durable_result: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> ToolInvocationRecord | None:
        async with self._lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "completed"
            record.result = deepcopy(durable_result)
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def release_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> ToolInvocationRecord | None:
        async with self._lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "pending"
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def renew_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> bool:
        async with self._lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return False
            record.updated_at = datetime.now(timezone.utc)
            return True


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
                    ToolInvocation.status == "pending",
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

    async def claim_processing(
        self,
        call_id: str,
        user_id: str,
        lease_timeout: timedelta | int | float = DEFAULT_PROCESSING_LEASE_TIMEOUT,
    ) -> ProcessingOutcome | None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session, session.begin():
            claim = await session.execute(
                update(ToolInvocation)
                .where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                    ToolInvocation.status == "pending",
                )
                .values(
                    status="processing",
                    version=ToolInvocation.version + 1,
                    updated_at=now,
                )
                .returning(ToolInvocation)
            )
            entity = claim.scalar_one_or_none()
            if entity is not None:
                return ProcessingOutcome(
                    record=self._record_from_entity(entity),
                    claimed=True,
                    claim_version=entity.version,
                )
            entity = await session.scalar(
                select(ToolInvocation).where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                )
            )
            return (
                ProcessingOutcome(
                    record=self._record_from_entity(entity),
                    claimed=False,
                    claim_version=entity.version,
                )
                if entity is not None
                else None
            )

    async def finish_processing(
        self,
        call_id: str,
        user_id: str,
        expected_version: int,
        durable_result: dict[str, Any],
        session: AsyncSession | None = None,
    ) -> ToolInvocationRecord | None:
        now = datetime.now(timezone.utc)
        statement = (
            update(ToolInvocation)
            .where(
                ToolInvocation.call_id == call_id,
                ToolInvocation.user_id == UUID(user_id),
                ToolInvocation.status == "processing",
                ToolInvocation.version == expected_version,
            )
            .values(
                status="completed",
                result=deepcopy(durable_result),
                version=ToolInvocation.version + 1,
                updated_at=now,
            )
            .returning(ToolInvocation)
        )
        if session is not None:
            result = await session.execute(statement)
            entity = result.scalar_one_or_none()
            return self._record_from_entity(entity) if entity is not None else None
        async with self.session_factory() as owned_session, owned_session.begin():
            result = await owned_session.execute(statement)
            entity = result.scalar_one_or_none()
            return self._record_from_entity(entity) if entity is not None else None

    async def release_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> ToolInvocationRecord | None:
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session, session.begin():
            result = await session.execute(
                update(ToolInvocation)
                .where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                    ToolInvocation.status == "processing",
                    ToolInvocation.version == expected_version,
                )
                .values(
                    status="pending",
                    version=ToolInvocation.version + 1,
                    updated_at=now,
                )
                .returning(ToolInvocation)
            )
            entity = result.scalar_one_or_none()
            return self._record_from_entity(entity) if entity is not None else None

    async def renew_processing(
        self, call_id: str, user_id: str, expected_version: int
    ) -> bool:
        async with self.session_factory() as session, session.begin():
            result = await session.execute(
                update(ToolInvocation)
                .where(
                    ToolInvocation.call_id == call_id,
                    ToolInvocation.user_id == UUID(user_id),
                    ToolInvocation.status == "processing",
                    ToolInvocation.version == expected_version,
                )
                .values(updated_at=datetime.now(timezone.utc))
                .returning(ToolInvocation.call_id)
            )
            return result.scalar_one_or_none() is not None

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

## Current tests/test_trip_form_tool_flow.py
```
import json
import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.api.dependencies import get_current_user
from app.governance.tool_invocations import CompletionOutcome, ToolInvocationRecord
from app.main import app
from app.schemas.planning import TravelRequirement
from app.schemas.tools import ToolResultRequest


class InMemoryInvocationRepository:
    def __init__(self, records):
        self.records = {record.call_id: record.model_copy(deep=True) for record in records}
        self.lock = asyncio.Lock()

    async def get_for_user(self, call_id, user_id):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        return record.model_copy(deep=True)

    async def update_partial(self, call_id, user_id, partial_values):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        record.partial_values = {**record.partial_values, **partial_values}
        record.version += 1
        return record.model_copy(deep=True)

    async def complete_once(self, call_id, user_id, result):
        record = self.records.get(call_id)
        if record is None or record.user_id != user_id:
            return None
        if record.status == "completed":
            return CompletionOutcome(record=record.model_copy(deep=True), completed_now=False)
        record.status = "completed"
        record.result = result
        record.version += 1
        return CompletionOutcome(record=record.model_copy(deep=True), completed_now=True)

    async def claim_processing(self, call_id, user_id, lease_timeout):
        async with self.lock:
            record = self.records.get(call_id)
            if record is None or record.user_id != user_id:
                return None
            if record.status != "pending":
                from app.governance.tool_invocations import ProcessingOutcome

                return ProcessingOutcome(
                    record=record.model_copy(deep=True),
                    claimed=False,
                    claim_version=record.version,
                )
            record.status = "processing"
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            from app.governance.tool_invocations import ProcessingOutcome

            return ProcessingOutcome(
                record=record.model_copy(deep=True),
                claimed=True,
                claim_version=record.version,
            )

    async def finish_processing(self, call_id, user_id, expected_version, durable_result, session=None):
        async with self.lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "completed"
            record.result = durable_result
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def release_processing(self, call_id, user_id, expected_version):
        async with self.lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return None
            record.status = "pending"
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def renew_processing(self, call_id, user_id, expected_version):
        async with self.lock:
            record = self.records.get(call_id)
            if (
                record is None
                or record.user_id != user_id
                or record.status != "processing"
                or record.version != expected_version
            ):
                return False
            record.updated_at = datetime.now(timezone.utc)
            return True


class RecordingSession:
    def __init__(self):
        self.messages = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def add(self, message):
        self.messages.append(message)

    async def commit(self):
        return None

    async def refresh(self, _message):
        return None

    def begin(self):
        return self


class RecordingEventRepository:
    def __init__(self):
        self.events = []

    async def append(self, event):
        event.sequence = len(self.events) + 1
        self.events.append(event)
        return event


class FakeDraft:
    def __init__(self, destination):
        self.destination = destination

    def model_dump(self, *, mode):
        assert mode == "json"
        return {"destination": self.destination, "summary": "confirmed itinerary"}


def invocation(*, user_id, status="pending", tool="collect_trip_requirements"):
    return ToolInvocationRecord(
        call_id="call-1",
        user_id=user_id,
        conversation_id=str(uuid4()),
        tool=tool,
        status=status,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def parse_sse(response):
    return [
        json.loads(frame.removeprefix("data: ").strip())
        for frame in response.text.split("\n\n")
        if frame.strip()
    ]


@asynccontextmanager
async def endpoint_client(user):
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def configure_endpoint(monkeypatch, repository):
    from app.api.v1 import tools

    session = RecordingSession()
    events = RecordingEventRepository()
    monkeypatch.setattr(tools, "PostgresToolInvocationRepository", lambda: repository, raising=False)
    monkeypatch.setattr(tools, "PostgresEventRepository", lambda: events, raising=False)
    monkeypatch.setattr(tools, "async_session_maker", lambda: session, raising=False)

    async def checkpointer():
        return None

    monkeypatch.setattr(tools, "get_checkpointer", checkpointer, raising=False)
    return session, events


def test_trip_form_result_route_is_registered():
    paths = {route.path for route in app.routes}

    assert "/api/v1/chat/tools/{call_id}/result" in paths


@pytest.mark.asyncio
async def test_tool_result_rejects_another_users_call(monkeypatch):
    owner_id = str(uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=owner_id)])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(SimpleNamespace(id=uuid4())) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": {
                "destination": "Kyoto", "departure_date": "2026-08-03", "days": 4,
            }},
        )

    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        None,
        {"destination": "Kyoto", "days": 4},
        {"destination": "Kyoto", "departure_date": "invalid", "days": 4},
        {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 0},
        {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4, "extra": True},
    ],
)
async def test_completed_result_requires_valid_trip_fields(monkeypatch, result):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": result},
        )

    assert response.status_code == 422
    assert repository.records["call-1"].status == "pending"


@pytest.mark.asyncio
async def test_tool_result_rejects_wrong_stored_tool(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository(
        [invocation(user_id=str(user.id), tool="other_tool")]
    )
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "recommend_destination"},
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_recommendation_requires_a_pending_call(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id), status="cancelled")])
    configure_endpoint(monkeypatch, repository)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "recommend_destination"},
        )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_recommendation_keeps_call_pending_and_returns_rag_answer(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    session, _ = configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    queries = []

    async def recommend(query):
        queries.append(query)
        return "Kyoto works well for a four-day food-focused trip."

    monkeypatch.setattr(tools, "answer_open_question", recommend, raising=False)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={
                "tool": "collect_trip_requirements",
                "status": "recommend_destination",
                "partial_values": {"days": 4, "interests": "food"},
            },
        )

    events = parse_sse(response)
    assert response.status_code == 200
    assert [event["type"] for event in events] == ["token", "tool_result", "done"]
    assert events[0]["content"] == "Kyoto works well for a four-day food-focused trip."
    assert events[1]["status"] == "awaiting_destination"
    assert repository.records["call-1"].status == "pending"
    assert repository.records["call-1"].partial_values == {"days": 4, "interests": "food"}
    assert queries and "food" in queries[0]
    assert session.messages[-1].extra_info["tool_result"]["status"] == "awaiting_destination"


@pytest.mark.asyncio
async def test_completed_result_invokes_supervisor_once_with_confirmed_fields(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    session, events = configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    calls = []

    async def supervisor(requirement, **kwargs):
        calls.append((requirement, kwargs))
        await kwargs["event_service"].emit(
            task_id=kwargs["task_id"],
            user_id=kwargs["user_id"],
            conversation_id=kwargs["conversation_id"],
            event_type="task_completed",
        )
        return FakeDraft(requirement.destination)

    monkeypatch.setattr(tools, "run_travel_planning", supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        response = await client.post("/api/v1/chat/tools/call-1/result", json=payload)
        duplicate_response = await client.post(
            "/api/v1/chat/tools/call-1/result", json=payload
        )

    stream = parse_sse(response)
    duplicate_stream = parse_sse(duplicate_response)
    assert [event["type"] for event in stream] == ["result", "token", "done"]
    assert len(calls) == 1
    requirement, kwargs = calls[0]
    assert isinstance(requirement, TravelRequirement)
    assert requirement.destination == "Kyoto"
    assert requirement.departure_date == date(2026, 8, 3)
    assert requirement.days == 4
    assert kwargs["task_id"] == "call-1"
    durable = repository.records["call-1"].result
    assert durable["confirmed_result"] == payload["result"]
    assert durable["task_id"] == "call-1"
    assert [
        (event["type"], event.get("payload")) for event in duplicate_stream
    ] == [
        (event["type"], event.get("payload")) for event in stream
    ]
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]
    assert events.events[-1].event_type == "task_completed"
    assert session.messages[-1].extra_info["tool_result"]["status"] == "completed"
    assert session.messages[-1].extra_info["assistant_result"] == stream[0]["payload"]["result"]


@pytest.mark.asyncio
async def test_duplicate_completed_result_uses_stored_completion_without_supervisor(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    confirmed = {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4}
    durable = {
        "confirmed_result": confirmed,
        "task_id": "call-1",
        "assistant_result": {"destination": "Kyoto", "summary": "confirmed itinerary"},
        "assistant_markdown": '{"destination": "Kyoto", "summary": "confirmed itinerary"}',
    }
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id), status="completed")])
    repository.records["call-1"].result = durable
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    async def unexpected_supervisor(*_args, **_kwargs):
        raise AssertionError("duplicate completion must not invoke Supervisor")

    monkeypatch.setattr(tools, "run_travel_planning", unexpected_supervisor, raising=False)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={"tool": "collect_trip_requirements", "status": "completed", "result": confirmed},
        )

    stream = parse_sse(response)
    assert response.status_code == 200
    assert stream[0]["type"] == "result"
    assert stream[0]["payload"]["result"] == durable["assistant_result"]


@pytest.mark.asyncio
async def test_supervisor_failure_releases_claim_and_sanitizes_retryable_error(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    async def failing_supervisor(*_args, **_kwargs):
        raise RuntimeError("database password leaked")

    monkeypatch.setattr(tools, "run_travel_planning", failing_supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        response = await client.post("/api/v1/chat/tools/call-1/result", json=payload)

    events = parse_sse(response)
    assert [event["type"] for event in events] == ["error", "done"]
    assert events[0]["payload"]["retryable"] is True
    assert "database password leaked" not in json.dumps(events[0])
    assert repository.records["call-1"].status == "pending"

    async def successful_supervisor(*_args, **_kwargs):
        return FakeDraft("Kyoto")

    monkeypatch.setattr(tools, "run_travel_planning", successful_supervisor, raising=False)

    async with endpoint_client(user) as client:
        retry = await client.post("/api/v1/chat/tools/call-1/result", json=payload)

    assert parse_sse(retry)[0]["type"] == "result"
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]


@pytest.mark.asyncio
async def test_active_processing_duplicate_returns_nonterminal_tool_result(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    active = invocation(user_id=str(user.id), status="processing")
    active.updated_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    repository = InMemoryInvocationRepository([active])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    async def unexpected_supervisor(*_args, **_kwargs):
        raise AssertionError("active processing duplicate must not invoke Supervisor")

    monkeypatch.setattr(tools, "run_travel_planning", unexpected_supervisor, raising=False)
    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={
                "tool": "collect_trip_requirements",
                "status": "completed",
                "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
            },
        )

    events = parse_sse(response)
    assert [event["type"] for event in events] == ["tool_result", "done"]
    assert events[0]["status"] == "processing"
    assert events[0]["payload"]["terminal"] is False


@pytest.mark.asyncio
async def test_heartbeat_keeps_long_supervisor_claim_owned(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    monkeypatch.setattr(tools, "PROCESSING_LEASE_TIMEOUT", timedelta(milliseconds=45))
    supervisor_started = asyncio.Event()
    supervisor_calls = []

    async def slow_supervisor(requirement, **_kwargs):
        supervisor_calls.append(requirement.destination)
        supervisor_started.set()
        await asyncio.sleep(0.12)
        return FakeDraft(requirement.destination)

    monkeypatch.setattr(tools, "run_travel_planning", slow_supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        first_request = asyncio.create_task(
            client.post("/api/v1/chat/tools/call-1/result", json=payload)
        )
        await asyncio.wait_for(supervisor_started.wait(), timeout=1)
        await asyncio.sleep(0.07)
        duplicate = await client.post("/api/v1/chat/tools/call-1/result", json=payload)
        first = await first_request

    duplicate_events = parse_sse(duplicate)
    assert [event["type"] for event in duplicate_events] == ["tool_result", "done"]
    assert duplicate_events[0]["status"] == "processing"
    assert supervisor_calls == ["Kyoto"]


@pytest.mark.asyncio
async def test_lease_loss_cancels_blocking_supervisor_before_second_claim_side_effect(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    monkeypatch.setattr(tools, "PROCESSING_LEASE_TIMEOUT", timedelta(milliseconds=30))
    started = asyncio.Event()
    block = asyncio.Event()
    side_effects = []

    async def lease_lost(*_args, **_kwargs):
        return False

    monkeypatch.setattr(repository, "renew_processing", lease_lost)

    async def supervisor(requirement, **_kwargs):
        side_effects.append("first-start" if len(side_effects) == 0 else "second-start")
        if len(side_effects) == 1:
            started.set()
            try:
                await block.wait()
            except asyncio.CancelledError:
                side_effects.append("first-cancel")
                raise
        return FakeDraft(requirement.destination)

    monkeypatch.setattr(tools, "run_travel_planning", supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        first_request = asyncio.create_task(
            client.post("/api/v1/chat/tools/call-1/result", json=payload)
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.sleep(0.05)
        second_request = asyncio.create_task(
            client.post("/api/v1/chat/tools/call-1/result", json=payload)
        )
        first = await asyncio.wait_for(first_request, timeout=1)
        second = await asyncio.wait_for(second_request, timeout=1)

    assert parse_sse(first)[0]["payload"]["code"] == "processing_conflict"
    assert parse_sse(second)[0]["type"] == "result"
    assert side_effects == ["first-start", "first-cancel", "second-start"]
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]


@pytest.mark.asyncio
async def test_heartbeat_lease_loss_prevents_finish_and_returns_retryable_conflict(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    monkeypatch.setattr(tools, "PROCESSING_LEASE_TIMEOUT", timedelta(milliseconds=30))

    async def lease_lost(*_args, **_kwargs):
        return False

    async def slow_supervisor(*_args, **_kwargs):
        await asyncio.sleep(0.04)
        return FakeDraft("Kyoto")

    monkeypatch.setattr(repository, "renew_processing", lease_lost)
    monkeypatch.setattr(tools, "run_travel_planning", slow_supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        response = await client.post("/api/v1/chat/tools/call-1/result", json=payload)

    event = parse_sse(response)[0]
    assert event["type"] == "error"
    assert event["payload"]["code"] == "processing_conflict"
    assert event["payload"]["retryable"] is True
    assert repository.records["call-1"].status == "pending"
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]


@pytest.mark.asyncio
async def test_heartbeat_exception_cancels_supervisor_and_releases_original_claim(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    monkeypatch.setattr(tools, "PROCESSING_LEASE_TIMEOUT", timedelta(milliseconds=30))
    release_calls = []
    original_release = repository.release_processing

    async def release(call_id, user_id, expected_version):
        release_calls.append((call_id, user_id, expected_version))
        return await original_release(call_id, user_id, expected_version)

    async def renewal_failure(*_args, **_kwargs):
        raise RuntimeError("renewal backend unavailable")

    supervisor_started = asyncio.Event()

    async def blocking_supervisor(*_args, **_kwargs):
        supervisor_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(repository, "release_processing", release)
    monkeypatch.setattr(repository, "renew_processing", renewal_failure)
    monkeypatch.setattr(tools, "run_travel_planning", blocking_supervisor, raising=False)
    payload = {
        "tool": "collect_trip_requirements",
        "status": "completed",
        "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
    }

    async with endpoint_client(user) as client:
        await asyncio.wait_for(
            client.post("/api/v1/chat/tools/call-1/result", json=payload), timeout=1
        )

    assert release_calls == [("call-1", str(user.id), 2)]
    assert repository.records["call-1"].status == "pending"


@pytest.mark.asyncio
async def test_client_cancellation_releases_claim_and_stops_heartbeat(monkeypatch):
    user = SimpleNamespace(id=uuid4())
    repository = InMemoryInvocationRepository([invocation(user_id=str(user.id))])
    session, _ = configure_endpoint(monkeypatch, repository)
    from app.api.v1 import tools

    started = asyncio.Event()

    async def never_finishes(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(tools, "run_travel_planning", never_finishes, raising=False)
    data = ToolResultRequest.model_validate(
        {
            "tool": "collect_trip_requirements",
            "status": "completed",
            "result": {"destination": "Kyoto", "departure_date": "2026-08-03", "days": 4},
        }
    )
    record = repository.records["call-1"].model_copy(deep=True)

    async def consume():
        async for _frame in tools.tool_result_stream("call-1", data, str(user.id), record):
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(started.wait(), timeout=1)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    assert repository.records["call-1"].status == "pending"
    assert session.messages == []
    assert not [
        task for task in asyncio.all_tasks() if task.get_name().startswith("tool-result-heartbeat:")
    ]
```

## Current tests/test_tool_invocations.py
```
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.governance.tool_invocations import (
    InMemoryToolInvocationRepository,
    PostgresToolInvocationRepository,
    ToolInvocationRecord,
)
from app.models.base import Base
import app.models  # noqa: F401


class FakeExecutionResult:
    def __init__(self, entity):
        self.entity = entity

    def scalar_one_or_none(self):
        return self.entity


class FakeSession:
    def __init__(self, *, scalar_results=(), update_entity=None):
        self.scalar_results = list(scalar_results)
        self.update_entity = update_entity
        self.added = []
        self.scalar_statements = []
        self.executed_statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return self

    async def scalar(self, statement):
        self.scalar_statements.append(statement)
        return self.scalar_results.pop(0)

    async def execute(self, statement):
        self.executed_statements.append(statement)
        return FakeExecutionResult(self.update_entity)

    def add(self, entity):
        self.added.append(entity)


class FakeSessionFactory:
    def __init__(self, *sessions):
        self.sessions = list(sessions)

    def __call__(self):
        return self.sessions.pop(0)


def postgres_entity(*, user_id, conversation_id, result, version=2, status="completed"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        call_id="c1",
        user_id=user_id,
        conversation_id=conversation_id,
        tool="collect_trip_requirements",
        status=status,
        arguments={"initial_values": {}},
        partial_values={},
        result=result,
        version=version,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_tool_result_is_idempotent():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
        )
    )

    first = await repository.complete_once(
        "c1",
        "u1",
        {"destination": "Chengdu", "departure_date": "2026-08-10", "days": 4},
    )
    second = await repository.complete_once("c1", "u1", first.record.result)

    assert first.completed_now is True
    assert first.record.status == "completed"
    assert second.completed_now is False
    assert second.record.version == first.record.version


@pytest.mark.asyncio
async def test_duplicate_completion_keeps_the_first_result():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
        )
    )

    first = await repository.complete_once("c1", "u1", {"destination": "Chengdu"})
    duplicate = await repository.complete_once("c1", "u1", {"destination": "Beijing"})

    assert duplicate.completed_now is False
    assert duplicate.record.result == first.record.result == {"destination": "Chengdu"}


@pytest.mark.asyncio
async def test_repository_records_are_isolated_from_caller_mutation():
    repository = InMemoryToolInvocationRepository()
    record = ToolInvocationRecord(
        call_id="c1",
        user_id="u1",
        conversation_id="v1",
        tool="collect_trip_requirements",
        arguments={"initial_values": {"destination": "Chengdu"}},
    )
    await repository.create(record)
    record.arguments["initial_values"]["destination"] = "Beijing"

    stored = await repository.get_for_user("c1", "u1")
    stored.arguments["initial_values"]["destination"] = "Shanghai"

    completed = await repository.complete_once("c1", "u1", {"days": [4]})
    completed.record.result["days"].append(5)
    reloaded = await repository.get_for_user("c1", "u1")

    assert reloaded.arguments == {"initial_values": {"destination": "Chengdu"}}
    assert reloaded.result == {"days": [4]}


@pytest.mark.asyncio
async def test_tool_call_is_user_scoped():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
        )
    )

    assert await repository.get_for_user("c1", "u2") is None


@pytest.mark.asyncio
async def test_partial_values_are_merged_for_the_owner_only():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
            partial_values={"destination": "Chengdu"},
        )
    )

    updated = await repository.update_partial("c1", "u1", {"days": 4})

    assert updated is not None
    assert updated.partial_values == {"destination": "Chengdu", "days": 4}
    assert await repository.update_partial("c1", "u2", {"days": 5}) is None


@pytest.mark.asyncio
async def test_partial_values_do_not_update_a_non_pending_call():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1",
            user_id="u1",
            conversation_id="v1",
            tool="collect_trip_requirements",
            status="processing",
        )
    )

    assert await repository.update_partial("c1", "u1", {"days": 4}) is None
    stored = await repository.get_for_user("c1", "u1")
    assert stored.partial_values == {}


@pytest.mark.asyncio
async def test_processing_claim_has_one_concurrent_winner():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements"
        )
    )

    outcomes = await asyncio.gather(
        repository.claim_processing("c1", "u1", timedelta(seconds=30)),
        repository.claim_processing("c1", "u1", timedelta(seconds=30)),
    )

    assert sum(outcome.claimed for outcome in outcomes) == 1
    assert all(outcome.record.status == "processing" for outcome in outcomes)
    winner = next(outcome for outcome in outcomes if outcome.claimed)
    loser = next(outcome for outcome in outcomes if not outcome.claimed)
    assert winner.claim_version == loser.claim_version


@pytest.mark.asyncio
async def test_stale_processing_claim_cannot_be_reclaimed_by_user_request():
    repository = InMemoryToolInvocationRepository()
    record = ToolInvocationRecord(
        call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements",
        status="processing", version=4,
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    await repository.create(record)

    outcome = await repository.claim_processing("c1", "u1", timedelta(seconds=30))

    assert outcome.claimed is False
    assert outcome.claim_version == 4
    assert outcome.record.status == "processing"


@pytest.mark.asyncio
async def test_processing_finish_and_release_require_matching_claim_version():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements"
        )
    )
    claim = await repository.claim_processing("c1", "u1", timedelta(seconds=30))

    assert await repository.finish_processing("c1", "u1", claim.claim_version - 1, {}) is None
    released = await repository.release_processing("c1", "u1", claim.claim_version)
    assert released.status == "pending"
    assert await repository.finish_processing("c1", "u1", claim.claim_version, {}) is None


@pytest.mark.asyncio
async def test_processing_renewal_requires_matching_version_and_status():
    repository = InMemoryToolInvocationRepository()
    await repository.create(
        ToolInvocationRecord(
            call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements"
        )
    )
    claim = await repository.claim_processing("c1", "u1", timedelta(seconds=30))

    assert await repository.renew_processing("c1", "u1", claim.claim_version) is True
    assert await repository.renew_processing("c1", "u1", claim.claim_version - 1) is False
    await repository.release_processing("c1", "u1", claim.claim_version)
    assert await repository.renew_processing("c1", "u1", claim.claim_version) is False

    await repository.create(
        ToolInvocationRecord(
            call_id="c2", user_id="u1", conversation_id="v1", tool="collect_trip_requirements"
        )
    )
    second_claim = await repository.claim_processing("c2", "u1", timedelta(seconds=30))
    repository.records["c2"].status = "completed"
    assert await repository.renew_processing("c2", "u1", second_claim.claim_version) is False
    assert await repository.release_processing("c2", "u1", second_claim.claim_version) is None


def test_tool_invocation_model_is_registered():
    assert "tool_invocation" in Base.metadata.tables


@pytest.mark.asyncio
async def test_postgres_create_rejects_conversation_not_owned_by_user():
    session = FakeSession(scalar_results=[None])
    repository = PostgresToolInvocationRepository(FakeSessionFactory(session))
    record = ToolInvocationRecord(
        call_id="c1",
        user_id=str(uuid4()),
        conversation_id=str(uuid4()),
        tool="collect_trip_requirements",
    )

    with pytest.raises(PermissionError):
        await repository.create(record)

    assert session.added == []
    assert len(session.scalar_statements) == 1


@pytest.mark.asyncio
async def test_postgres_create_in_session_uses_the_callers_transaction_and_checks_ownership():
    user_id = uuid4()
    conversation_id = uuid4()
    session = FakeSession(scalar_results=[conversation_id])
    repository = PostgresToolInvocationRepository()
    record = ToolInvocationRecord(
        call_id="c1",
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        tool="collect_trip_requirements",
        arguments={"initial_values": {"destination": "Chengdu"}},
    )

    created = await repository.create_in_session(session, record)

    assert created == record
    assert len(session.scalar_statements) == 1
    assert len(session.added) == 1
    assert session.added[0].call_id == "c1"
    assert session.added[0].user_id == user_id
    assert session.added[0].conversation_id == conversation_id


@pytest.mark.asyncio
async def test_postgres_completion_marks_only_returned_update_row_as_newly_completed():
    user_id = uuid4()
    conversation_id = uuid4()
    result = {"destination": "Chengdu"}
    claimed_session = FakeSession(
        update_entity=postgres_entity(
            user_id=user_id,
            conversation_id=conversation_id,
            result=result,
        )
    )
    duplicate_session = FakeSession(
        scalar_results=[
            postgres_entity(
                user_id=user_id,
                conversation_id=conversation_id,
                result=result,
            )
        ]
    )
    repository = PostgresToolInvocationRepository(
        FakeSessionFactory(claimed_session, duplicate_session)
    )

    claimed = await repository.complete_once("c1", str(user_id), result)
    duplicate = await repository.complete_once("c1", str(user_id), {"destination": "Beijing"})

    assert claimed.completed_now is True
    assert duplicate.completed_now is False
    assert duplicate.record.result == result
    assert len(claimed_session.executed_statements) == 1
    assert len(duplicate_session.executed_statements) == 1
    completion_sql = str(
        claimed_session.executed_statements[0].compile(dialect=postgresql.dialect())
    )
    assert "tool_invocation.call_id =" in completion_sql
    assert "tool_invocation.user_id =" in completion_sql
    assert "tool_invocation.status !=" in completion_sql


@pytest.mark.asyncio
async def test_postgres_processing_claim_only_claims_pending_records():
    user_id = uuid4()
    conversation_id = uuid4()
    processing = postgres_entity(
        user_id=user_id,
        conversation_id=conversation_id,
        result=None,
        version=3,
        status="processing",
    )
    session = FakeSession(
        scalar_results=[processing],
    )
    repository = PostgresToolInvocationRepository(FakeSessionFactory(session))

    outcome = await repository.claim_processing("c1", str(user_id), timedelta(seconds=30))

    assert outcome.claimed is False
    assert outcome.claim_version == 3
    assert outcome.record.status == "processing"
    claim_sql = str(session.executed_statements[0].compile(dialect=postgresql.dialect()))
    assert "tool_invocation.status" in claim_sql
    assert "tool_invocation.updated_at <=" not in claim_sql
    assert "tool_invocation.version +" in claim_sql


@pytest.mark.asyncio
async def test_postgres_finish_and_release_require_processing_version_match():
    user_id = uuid4()
    conversation_id = uuid4()
    finish_session = FakeSession(
        update_entity=postgres_entity(
            user_id=user_id,
            conversation_id=conversation_id,
            result={"task_id": "c1"},
            version=5,
            status="completed",
        )
    )
    release_session = FakeSession(
        update_entity=postgres_entity(
            user_id=user_id,
            conversation_id=conversation_id,
            result=None,
            version=5,
            status="pending",
        )
    )
    repository = PostgresToolInvocationRepository(
        FakeSessionFactory(finish_session, release_session)
    )

    finished = await repository.finish_processing("c1", str(user_id), 4, {"task_id": "c1"})
    released = await repository.release_processing("c1", str(user_id), 4)

    assert finished.status == "completed"
    assert released.status == "pending"
    finish_sql = str(finish_session.executed_statements[0].compile(dialect=postgresql.dialect()))
    release_sql = str(release_session.executed_statements[0].compile(dialect=postgresql.dialect()))
    assert "tool_invocation.version =" in finish_sql
    assert "tool_invocation.status =" in finish_sql
    assert "tool_invocation.version =" in release_sql


@pytest.mark.asyncio
async def test_postgres_processing_renewal_requires_processing_version_match():
    user_id = uuid4()
    conversation_id = uuid4()
    session = FakeSession(
        update_entity=postgres_entity(
            user_id=user_id,
            conversation_id=conversation_id,
            result=None,
            version=5,
            status="processing",
        )
    )
    repository = PostgresToolInvocationRepository(FakeSessionFactory(session))

    renewed = await repository.renew_processing("c1", str(user_id), 5)

    assert renewed is True
    renewal_sql = str(session.executed_statements[0].compile(dialect=postgresql.dialect()))
    assert "tool_invocation.status =" in renewal_sql
    assert "tool_invocation.version =" in renewal_sql
    assert "updated_at" in renewal_sql
```
