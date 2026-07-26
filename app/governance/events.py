"""可持久化任务事件的业务接口。"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from app.schemas.governance import TaskEventRecord
from app.schemas.events import SSEEvent


class EventRepository(Protocol):
    async def append(self, event: TaskEventRecord) -> TaskEventRecord: ...
    async def list(self, task_id: str, user_id: str) -> list[TaskEventRecord]: ...


class InMemoryEventRepository:
    def __init__(self):
        self.events: dict[str, list[TaskEventRecord]] = {}
        self._lock = asyncio.Lock()

    async def append(self, event: TaskEventRecord) -> TaskEventRecord:
        async with self._lock:
            bucket = self.events.setdefault(event.task_id, [])
            event.sequence = len(bucket) + 1
            bucket.append(event.model_copy(deep=True))
        return event

    async def list(self, task_id: str, user_id: str) -> list[TaskEventRecord]:
        return [item.model_copy(deep=True) for item in self.events.get(task_id, []) if item.user_id == user_id]


class TaskEventService:
    def __init__(self, repository: EventRepository):
        self.repository = repository

    async def emit(
        self,
        *,
        task_id: str,
        user_id: str,
        event_type: str,
        payload: dict | None = None,
        conversation_id: str | None = None,
    ) -> TaskEventRecord:
        return await self.repository.append(
            TaskEventRecord(
                task_id=task_id,
                conversation_id=conversation_id,
                user_id=user_id,
                event_type=event_type,
                sequence=1,
                payload=payload or {},
            )
        )


_PUBLIC_RESEARCH_EVENT_TYPES = {
    "worker_started": "subagent_started",
    "subagent_started": "subagent_started",
    "worker_completed": "subagent_completed",
    "subagent_completed": "subagent_completed",
    "evidence_collected": "evidence_collected",
    "subagent_tool_called": "subagent_tool_call",
    "tool_called": "subagent_tool_call",
    "follow_up_search": "follow_up_search",
    "follow_up_search_started": "follow_up_search",
    "research_conflict": "research_conflict",
    "research_conflict_detected": "research_conflict",
    "conflict_detected": "research_conflict",
    "subagent_tool_completed": "subagent_tool_completed",
}


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _warning_code(warning: str) -> str:
    value = warning.lower()
    if "max round" in value:
        return "max_rounds"
    if "tool call" in value:
        return "tool_call_limit"
    if "timeout" in value:
        return "timeout"
    if "duplicate" in value:
        return "duplicate_follow_up"
    if "conflict" in value:
        return "conflict"
    if "unbound" in value:
        return "unbound_claim"
    if "search failed" in value:
        return "search_failed"
    if "provider" in value or "unavailable" in value:
        return "provider_unavailable"
    return "warning"


def _warning_codes(payload: dict[str, Any]) -> list[str]:
    explicit = payload.get("warning_codes")
    if isinstance(explicit, list):
        safe_codes: list[str] = []
        for value in explicit:
            code = str(value)
            safe_codes.append(code if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else "warning")
        return _dedupe(safe_codes)
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        return _dedupe(
            [code for code in (_warning_code(str(value)) for value in warnings) if code != "warning"]
        )
    return []


def _count_from_payload(payload: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, list):
            return len(value)
    return None


def _public_research_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {}
    for key in ("task_id", "worker"):
        if payload.get(key) is not None:
            public[key] = payload[key]
    tool_name = payload.get("tool_name") or payload.get("tool")
    if tool_name is not None and event_type in {
        "subagent_tool_call",
        "follow_up_search",
        "subagent_tool_completed",
    }:
        public["tool_name"] = str(tool_name)
    round_number = payload.get("round_number", payload.get("round"))
    if (
        isinstance(round_number, int)
        and round_number > 0
        and event_type in {"subagent_tool_call", "follow_up_search", "subagent_tool_completed"}
    ):
        public["round_number"] = round_number
    status = payload.get("status")
    allowed_statuses = {
        "subagent_completed": {"completed", "partial", "unavailable", "failed"},
        "subagent_tool_completed": {"sufficient", "partial", "empty", "failed"},
    }.get(event_type, set())
    if status in allowed_statuses:
        public["status"] = status

    evidence_count = _count_from_payload(payload, "evidence_count", "count", "evidence")
    if (
        isinstance(evidence_count, int)
        and evidence_count >= 0
        and event_type in {"evidence_collected", "subagent_completed", "subagent_tool_completed"}
    ):
        public["evidence_count"] = evidence_count

    conflict_count = _count_from_payload(payload, "conflict_count", "conflicts", "count")
    if conflict_count is not None and event_type in {"research_conflict", "subagent_completed"}:
        public["conflict_count"] = conflict_count

    warning_codes = _warning_codes(payload)
    if warning_codes and event_type in {"subagent_completed", "subagent_tool_completed"}:
        public["warning_codes"] = warning_codes
    return public


def task_event_to_sse_event(event: TaskEventRecord) -> SSEEvent | None:
    """Map durable task events to public SSE metadata without reasoning or evidence text."""
    sse_type = _PUBLIC_RESEARCH_EVENT_TYPES.get(event.event_type)
    if sse_type is None:
        return None
    return SSEEvent(
        type=sse_type,
        task_id=event.task_id,
        conversation_id=event.conversation_id,
        sequence=event.sequence,
        payload=_public_research_payload(sse_type, event.payload),
    )


class PublishingEventRepository:
    """持久化事件后同步发布给当前 SSE 消费者。"""

    def __init__(self, inner: EventRepository, publish: Callable[[TaskEventRecord], Awaitable[None]]):
        self.inner = inner
        self.publish = publish

    async def append(self, event: TaskEventRecord) -> TaskEventRecord:
        stored = await self.inner.append(event)
        await self.publish(stored)
        return stored

    async def list(self, task_id: str, user_id: str) -> list[TaskEventRecord]:
        return await self.inner.list(task_id, user_id)
