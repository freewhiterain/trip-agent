"""审批、事件、偏好和正式行程的 PostgreSQL 持久化仓库。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from app.models.base import async_session_maker
from app.models.governance import Approval, SavedItinerary, TaskEvent, UserPreference
from app.schemas.governance import ApprovalRecord, PreferenceRecord, TaskEventRecord


class PostgresApprovalRepository:
    def __init__(self, session_factory=async_session_maker):
        self.session_factory = session_factory

    async def save(self, record: ApprovalRecord) -> ApprovalRecord:
        async with self.session_factory() as session, session.begin():
            entity = await session.get(Approval, UUID(record.id))
            values = record.model_dump(exclude={"id"})
            values["user_id"] = UUID(record.user_id)
            if entity is None:
                entity = Approval(id=UUID(record.id), **values)
                session.add(entity)
            else:
                for key, value in values.items():
                    setattr(entity, key, value)
        return record

    async def get(self, approval_id: str) -> ApprovalRecord | None:
        async with self.session_factory() as session:
            entity = await session.get(Approval, UUID(approval_id))
            if entity is None:
                return None
            return ApprovalRecord(
                id=str(entity.id), task_id=entity.task_id, user_id=str(entity.user_id),
                action=entity.action, payload=entity.payload, status=entity.status,
                decision_payload=entity.decision_payload, created_at=entity.created_at,
                decided_at=entity.decided_at,
            )


class PostgresEventRepository:
    def __init__(self, session_factory=async_session_maker):
        self.session_factory = session_factory

    async def append(self, event: TaskEventRecord) -> TaskEventRecord:
        async with self.session_factory() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:task_id))"),
                {"task_id": event.task_id},
            )
            sequence = await session.scalar(
                select(func.coalesce(func.max(TaskEvent.sequence), 0) + 1).where(TaskEvent.task_id == event.task_id)
            )
            event.sequence = int(sequence or 1)
            session.add(
                TaskEvent(
                    id=UUID(event.id), task_id=event.task_id,
                    conversation_id=event.conversation_id, user_id=UUID(event.user_id),
                    event_type=event.event_type, sequence=event.sequence,
                    payload=event.payload, created_at=event.created_at,
                )
            )
        return event

    async def list(self, task_id: str, user_id: str) -> list[TaskEventRecord]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(TaskEvent)
                .where(TaskEvent.task_id == task_id, TaskEvent.user_id == UUID(user_id))
                .order_by(TaskEvent.sequence)
            )
            return [
                TaskEventRecord(
                    id=str(item.id), task_id=item.task_id,
                    conversation_id=item.conversation_id, user_id=str(item.user_id),
                    event_type=item.event_type, sequence=item.sequence,
                    payload=item.payload, created_at=item.created_at,
                )
                for item in result.scalars()
            ]


class PostgresPreferenceRepository:
    def __init__(self, session_factory=async_session_maker):
        self.session_factory = session_factory

    async def upsert(self, record: PreferenceRecord) -> PreferenceRecord:
        now = datetime.now(timezone.utc)
        statement = (
            insert(UserPreference)
            .values(
                id=UUID(record.id), user_id=UUID(record.user_id), key=record.key,
                value=record.value, source=record.source,
                confirmed_at=record.confirmed_at, updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_user_preference_key",
                set_={"value": record.value, "source": record.source, "updated_at": now},
            )
            .returning(UserPreference)
        )
        async with self.session_factory() as session, session.begin():
            entity = (await session.execute(statement)).scalar_one()
            return PreferenceRecord(
                id=str(entity.id), user_id=str(entity.user_id), key=entity.key,
                value=entity.value, source=entity.source,
                confirmed_at=entity.confirmed_at, updated_at=entity.updated_at,
            )

    async def delete(self, user_id: str, key: str) -> bool:
        async with self.session_factory() as session, session.begin():
            entity = await session.scalar(
                select(UserPreference).where(UserPreference.user_id == UUID(user_id), UserPreference.key == key)
            )
            if entity is None:
                return False
            await session.delete(entity)
            return True

    async def list(self, user_id: str) -> list[PreferenceRecord]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(UserPreference).where(UserPreference.user_id == UUID(user_id)).order_by(UserPreference.key)
            )
            return [
                PreferenceRecord(
                    id=str(item.id), user_id=str(item.user_id), key=item.key,
                    value=item.value, source=item.source,
                    confirmed_at=item.confirmed_at, updated_at=item.updated_at,
                )
                for item in result.scalars()
            ]


class PostgresItineraryRepository:
    def __init__(self, session_factory=async_session_maker):
        self.session_factory = session_factory

    async def save(self, user_id: str, conversation_id: str, title: str, content: dict) -> dict:
        lock_key = f"{user_id}:{conversation_id}"
        async with self.session_factory() as session, session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": lock_key})
            version = await session.scalar(
                select(func.coalesce(func.max(SavedItinerary.version), 0) + 1).where(
                    SavedItinerary.user_id == UUID(user_id),
                    SavedItinerary.conversation_id == UUID(conversation_id),
                )
            )
            entity = SavedItinerary(
                user_id=UUID(user_id), conversation_id=UUID(conversation_id),
                version=int(version or 1), title=title, content=content, status="confirmed",
            )
            session.add(entity)
            await session.flush()
            return {"id": str(entity.id), "user_id": user_id, "conversation_id": conversation_id, "version": entity.version, "title": title, "content": content}

    async def get(self, user_id: str, conversation_id: str) -> dict | None:
        async with self.session_factory() as session:
            entity = await session.scalar(
                select(SavedItinerary)
                .where(SavedItinerary.user_id == UUID(user_id), SavedItinerary.conversation_id == UUID(conversation_id))
                .order_by(SavedItinerary.version.desc())
                .limit(1)
            )
            if entity is None:
                return None
            return {"id": str(entity.id), "user_id": user_id, "conversation_id": conversation_id, "version": entity.version, "title": entity.title, "content": entity.content}
