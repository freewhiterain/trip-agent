# Task 2 Review Package

No commits by user instruction. Full current contents of task-owned files follow.

## app/models/tool_invocation.py
```
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ToolInvocation(Base):
    __tablename__ = "tool_invocation"
    __table_args__ = (UniqueConstraint("call_id", name="uq_tool_invocation_call_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id: Mapped[str] = mapped_column(String(80), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversation.id", ondelete="CASCADE"), index=True
    )
    tool: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)
    partial_values: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), onupdate=func.now()
    )
```

## app/governance/tool_invocations.py
```
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select, update

from app.models.base import async_session_maker
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


class ToolInvocationRepository(Protocol):
    async def create(self, record: ToolInvocationRecord) -> ToolInvocationRecord: ...
    async def get_for_user(self, call_id: str, user_id: str) -> ToolInvocationRecord | None: ...
    async def update_partial(
        self, call_id: str, user_id: str, partial_values: dict[str, Any]
    ) -> ToolInvocationRecord | None: ...
    async def complete_once(
        self, call_id: str, user_id: str, result: dict[str, Any] | None
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
            if record is None or record.user_id != user_id:
                return None
            record.partial_values = {**record.partial_values, **deepcopy(partial_values)}
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)

    async def complete_once(
        self, call_id: str, user_id: str, result: dict[str, Any] | None
    ) -> ToolInvocationRecord | None:
        async with self._lock:
            record = self.records.get(call_id)
            if record is None or record.user_id != user_id:
                return None
            if record.status == "completed":
                return record.model_copy(deep=True)
            record.status = "completed"
            record.result = deepcopy(result)
            record.version += 1
            record.updated_at = datetime.now(timezone.utc)
            return record.model_copy(deep=True)


class PostgresToolInvocationRepository:
    def __init__(self, session_factory=async_session_maker):
        self.session_factory = session_factory

    async def create(self, record: ToolInvocationRecord) -> ToolInvocationRecord:
        async with self.session_factory() as session, session.begin():
            session.add(
                ToolInvocation(
                    call_id=record.call_id,
                    user_id=UUID(record.user_id),
                    conversation_id=UUID(record.conversation_id),
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
    ) -> ToolInvocationRecord | None:
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
            return self._record_from_entity(entity) if entity else None

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

## app/models/__init__.py
```
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.draft import TripDraft
from app.models.governance import Approval, SavedItinerary, TaskEvent, UserPreference
from app.models.message import Message
from app.models.tool_invocation import ToolInvocation
from app.models.user import User

__all__ = [
    "Approval",
    "Base",
    "Conversation",
    "Message",
    "SavedItinerary",
    "TaskEvent",
    "ToolInvocation",
    "TripDraft",
    "User",
    "UserPreference",
]
```

## tests/test_tool_invocations.py
```
import pytest

from app.governance.tool_invocations import InMemoryToolInvocationRepository, ToolInvocationRecord
from app.models.base import Base
import app.models  # noqa: F401


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
    second = await repository.complete_once("c1", "u1", first.result)

    assert first.status == "completed"
    assert second.version == first.version


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


def test_tool_invocation_model_is_registered():
    assert "tool_invocation" in Base.metadata.tables
```

# Fix Addendum

## Updated app/governance/tool_invocations.py
```
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select, update

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
        async with self.session_factory() as session, session.begin():
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

## Updated tests/test_tool_invocations.py
```
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

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


def postgres_entity(*, user_id, conversation_id, result, version=2):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        call_id="c1",
        user_id=user_id,
        conversation_id=conversation_id,
        tool="collect_trip_requirements",
        status="completed",
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
```

# PostgreSQL Test Addendum

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
        async with self.session_factory() as session, session.begin():
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

## Current tests/test_tool_invocations.py
```
from datetime import datetime, timezone
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


def postgres_entity(*, user_id, conversation_id, result, version=2):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        call_id="c1",
        user_id=user_id,
        conversation_id=conversation_id,
        tool="collect_trip_requirements",
        status="completed",
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
```

## Current tests/test_tool_invocations_postgres.py
```
import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

import app.models  # noqa: F401
from app.governance.tool_invocations import PostgresToolInvocationRepository, ToolInvocationRecord
from app.models.base import async_session_maker, init_db
from app.models.conversation import Conversation
from app.models.tool_invocation import ToolInvocation
from app.models.user import User


pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_TESTS") != "1",
        reason="requires RUN_POSTGRES_TESTS=1 and a reachable PostgreSQL database",
    ),
]


@pytest.mark.asyncio
async def test_postgres_completion_is_atomic_for_conflicting_results():
    await init_db()
    user_id = uuid4()
    conversation_id = uuid4()
    call_id = str(uuid4())
    token = uuid4().hex
    first_result = {"destination": "Chengdu", "days": 4}
    second_result = {"destination": "Beijing", "days": 5}

    try:
        async with async_session_maker() as session, session.begin():
            session.add_all(
                [
                    User(
                        id=user_id,
                        username=f"tooltest-{token}",
                        email=f"tooltest-{token}@example.test",
                        password_hash="test-only",
                    ),
                    Conversation(
                        id=conversation_id,
                        user_id=user_id,
                        title="tool invocation concurrency test",
                    ),
                ]
            )

        repository = PostgresToolInvocationRepository()
        await repository.create(
            ToolInvocationRecord(
                call_id=call_id,
                user_id=str(user_id),
                conversation_id=str(conversation_id),
                tool="collect_trip_requirements",
            )
        )

        outcomes = await asyncio.gather(
            repository.complete_once(call_id, str(user_id), first_result),
            repository.complete_once(call_id, str(user_id), second_result),
        )

        assert all(outcome is not None for outcome in outcomes)
        assert sum(outcome.completed_now for outcome in outcomes) == 1
        winner = next(outcome.record.result for outcome in outcomes if outcome.completed_now)
        assert all(outcome.record.result == winner for outcome in outcomes)
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(ToolInvocation).where(ToolInvocation.call_id == call_id))
            await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
            await session.execute(delete(User).where(User.id == user_id))
```

# Final Concurrency Test Addendum
```python
import asyncio
import os
from uuid import uuid4

import pytest
from sqlalchemy import delete

import app.models  # noqa: F401
from app.governance.tool_invocations import PostgresToolInvocationRepository, ToolInvocationRecord
from app.models.base import async_session_maker, init_db
from app.models.conversation import Conversation
from app.models.tool_invocation import ToolInvocation
from app.models.user import User


pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_TESTS") != "1",
        reason="requires RUN_POSTGRES_TESTS=1 and a reachable PostgreSQL database",
    ),
]


class BarrierSession:
    def __init__(self, session, barrier):
        self.session = session
        self.barrier = barrier

    async def __aenter__(self):
        await self.session.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return await self.session.__aexit__(exc_type, exc, traceback)

    def begin(self):
        return self.session.begin()

    async def execute(self, statement, *args, **kwargs):
        if getattr(statement, "table", None) is ToolInvocation.__table__:
            await self.barrier.wait()
        return await self.session.execute(statement, *args, **kwargs)

    async def scalar(self, statement, *args, **kwargs):
        return await self.session.scalar(statement, *args, **kwargs)


class BarrierSessionFactory:
    def __init__(self, barrier):
        self.barrier = barrier

    def __call__(self):
        return BarrierSession(async_session_maker(), self.barrier)


@pytest.mark.asyncio
async def test_postgres_completion_is_atomic_for_conflicting_results():
    await init_db()
    user_id = uuid4()
    conversation_id = uuid4()
    call_id = str(uuid4())
    token = uuid4().hex
    first_result = {"destination": "Chengdu", "days": 4}
    second_result = {"destination": "Beijing", "days": 5}

    try:
        async with async_session_maker() as session, session.begin():
            session.add_all(
                [
                    User(
                        id=user_id,
                        username=f"tooltest-{token}",
                        email=f"tooltest-{token}@example.test",
                        password_hash="test-only",
                    ),
                    Conversation(
                        id=conversation_id,
                        user_id=user_id,
                        title="tool invocation concurrency test",
                    ),
                ]
            )

        repository = PostgresToolInvocationRepository()
        await repository.create(
            ToolInvocationRecord(
                call_id=call_id,
                user_id=str(user_id),
                conversation_id=str(conversation_id),
                tool="collect_trip_requirements",
            )
        )

        completion_repository = PostgresToolInvocationRepository(
            BarrierSessionFactory(asyncio.Barrier(2))
        )
        outcomes = await asyncio.wait_for(
            asyncio.gather(
                completion_repository.complete_once(call_id, str(user_id), first_result),
                completion_repository.complete_once(call_id, str(user_id), second_result),
            ),
            timeout=10,
        )

        assert all(outcome is not None for outcome in outcomes)
        assert sum(outcome.completed_now for outcome in outcomes) == 1
        winner = next(outcome.record.result for outcome in outcomes if outcome.completed_now)
        assert winner is not None
        assert winner in (first_result, second_result)
        reloaded = await repository.get_for_user(call_id, str(user_id))
        assert reloaded is not None
        assert reloaded.result == winner
        assert all(outcome.record.result == reloaded.result for outcome in outcomes)
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(ToolInvocation).where(ToolInvocation.call_id == call_id))
            await session.execute(delete(Conversation).where(Conversation.id == conversation_id))
            await session.execute(delete(User).where(User.id == user_id))
```
