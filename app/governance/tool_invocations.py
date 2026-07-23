from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import async_session_maker, engine
from app.models.conversation import Conversation
from app.models.tool_invocation import ToolInvocation


DEFAULT_PROCESSING_LEASE_TIMEOUT = timedelta(minutes=2)
PROCESSING_RECOVERY_LOCK_ID = 0x54524950


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
    async def release_stale_processing(
        self, lease_timeout: timedelta | int | float = DEFAULT_PROCESSING_LEASE_TIMEOUT
    ) -> int: ...
    async def acquire_processing_guard(self) -> Any: ...
    async def release_processing_guard(self, guard: Any) -> None: ...


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

    async def release_stale_processing(
        self, lease_timeout: timedelta | int | float = DEFAULT_PROCESSING_LEASE_TIMEOUT
    ) -> int:
        timeout = lease_timeout if isinstance(lease_timeout, timedelta) else timedelta(seconds=float(lease_timeout))
        cutoff = datetime.now(timezone.utc) - timeout
        recovered = 0
        async with self._lock:
            for record in self.records.values():
                if record.status == "processing" and record.updated_at <= cutoff:
                    record.status = "pending"
                    record.version += 1
                    record.updated_at = datetime.now(timezone.utc)
                    recovered += 1
        return recovered

    async def acquire_processing_guard(self) -> None:
        return None

    async def release_processing_guard(self, guard: Any) -> None:
        return None


class PostgresToolInvocationRepository:
    def __init__(self, session_factory=async_session_maker, db_engine=engine):
        self.session_factory = session_factory
        self.engine = db_engine

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

    async def release_stale_processing(
        self, lease_timeout: timedelta | int | float = DEFAULT_PROCESSING_LEASE_TIMEOUT
    ) -> int:
        timeout = lease_timeout if isinstance(lease_timeout, timedelta) else timedelta(seconds=float(lease_timeout))
        now = datetime.now(timezone.utc)
        async with self.session_factory() as session, session.begin():
            locked = await session.scalar(
                text("SELECT pg_try_advisory_xact_lock(:lock_id)").bindparams(
                    lock_id=PROCESSING_RECOVERY_LOCK_ID
                )
            )
            if not locked:
                raise RuntimeError("Another tool invocation recovery is already running")
            result = await session.execute(
                update(ToolInvocation)
                .where(
                    ToolInvocation.status == "processing",
                    ToolInvocation.updated_at <= now - timeout,
                )
                .values(
                    status="pending",
                    version=ToolInvocation.version + 1,
                    updated_at=now,
                )
            )
            return result.rowcount

    async def acquire_processing_guard(self) -> Any:
        connection = await self.engine.connect()
        try:
            await connection.execute(
                text("SELECT pg_advisory_lock_shared(:lock_id)").bindparams(
                    lock_id=PROCESSING_RECOVERY_LOCK_ID
                )
            )
            return connection
        except BaseException:
            await connection.close()
            raise

    async def release_processing_guard(self, guard: Any) -> None:
        try:
            await guard.execute(
                text("SELECT pg_advisory_unlock_shared(:lock_id)").bindparams(
                    lock_id=PROCESSING_RECOVERY_LOCK_ID
                )
            )
        finally:
            await guard.close()

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
