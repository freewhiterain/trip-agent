"""可持久化任务事件的业务接口。"""

from __future__ import annotations

import asyncio
from typing import Protocol

from app.schemas.governance import TaskEventRecord


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
