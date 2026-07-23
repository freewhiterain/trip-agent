"""会话级行程草稿仓库：唯一 owner 是对话协调器。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models.base import async_session_maker
from app.models.draft import TripDraft
from app.schemas.planning import TripDraftRecord


class DraftRepository(Protocol):
    async def get(self, user_id: str, conversation_id: str) -> TripDraftRecord | None: ...
    async def save(self, record: TripDraftRecord) -> TripDraftRecord: ...


class InMemoryDraftRepository:
    def __init__(self):
        self.records: dict[tuple[str, str], TripDraftRecord] = {}

    async def get(self, user_id: str, conversation_id: str) -> TripDraftRecord | None:
        record = self.records.get((user_id, conversation_id))
        return record.model_copy(deep=True) if record else None

    async def save(self, record: TripDraftRecord) -> TripDraftRecord:
        key = (record.user_id, record.conversation_id)
        existing = self.records.get(key)
        record.version = existing.version + 1 if existing else 1
        self.records[key] = record.model_copy(deep=True)
        return record


class PostgresDraftRepository:
    def __init__(self, session_factory=async_session_maker):
        self.session_factory = session_factory

    async def get(self, user_id: str, conversation_id: str) -> TripDraftRecord | None:
        async with self.session_factory() as session:
            entity = await session.scalar(
                select(TripDraft)
                .where(
                    TripDraft.user_id == UUID(user_id),
                    TripDraft.conversation_id == UUID(conversation_id),
                )
                .limit(1)
            )
            if entity is None:
                return None
            return TripDraftRecord(
                user_id=user_id,
                conversation_id=conversation_id,
                version=entity.version,
                requirement=entity.requirement,
                content=entity.content,
            )

    async def save(self, record: TripDraftRecord) -> TripDraftRecord:
        now = datetime.now(timezone.utc)
        statement = (
            insert(TripDraft)
            .values(
                user_id=UUID(record.user_id),
                conversation_id=UUID(record.conversation_id),
                version=1,
                requirement=record.requirement,
                content=record.content,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_trip_draft_conversation",
                set_={
                    "requirement": record.requirement,
                    "content": record.content,
                    "version": TripDraft.version + 1,
                    "updated_at": now,
                },
            )
            .returning(TripDraft.version)
        )
        async with self.session_factory() as session, session.begin():
            record.version = int(await session.scalar(statement))
        return record
