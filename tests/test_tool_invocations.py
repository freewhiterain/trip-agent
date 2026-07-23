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
    def __init__(self, entity, rowcount=0):
        self.entity = entity
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self.entity


class FakeSession:
    def __init__(self, *, scalar_results=(), update_entity=None, rowcount=0):
        self.scalar_results = list(scalar_results)
        self.update_entity = update_entity
        self.rowcount = rowcount
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
        return FakeExecutionResult(self.update_entity, self.rowcount)

    def add(self, entity):
        self.added.append(entity)


class FakeSessionFactory:
    def __init__(self, *sessions):
        self.sessions = list(sessions)

    def __call__(self):
        return self.sessions.pop(0)


class FakeConnection:
    def __init__(self):
        self.statements = []
        self.closed = False

    async def execute(self, statement):
        self.statements.append(statement)

    async def close(self):
        self.closed = True


class FakeEngine:
    def __init__(self, connection):
        self.connection = connection

    async def connect(self):
        return self.connection


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
async def test_startup_recovery_releases_only_expired_processing_claims():
    repository = InMemoryToolInvocationRepository()
    now = datetime.now(timezone.utc)
    await repository.create(
        ToolInvocationRecord(
            call_id="stale", user_id="u1", conversation_id="v1",
            tool="collect_trip_requirements", status="processing", version=4,
            updated_at=now - timedelta(minutes=5),
        )
    )
    await repository.create(
        ToolInvocationRecord(
            call_id="active", user_id="u1", conversation_id="v1",
            tool="collect_trip_requirements", status="processing", version=7,
            updated_at=now,
        )
    )

    recovered = await repository.release_stale_processing(timedelta(minutes=2))

    stale = await repository.get_for_user("stale", "u1")
    active = await repository.get_for_user("active", "u1")
    assert recovered == 1
    assert stale.status == "pending"
    assert stale.version == 5
    assert active.status == "processing"
    assert active.version == 7


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


@pytest.mark.asyncio
async def test_postgres_admin_recovery_uses_stale_processing_predicate():
    session = FakeSession(scalar_results=[True], rowcount=2)
    repository = PostgresToolInvocationRepository(FakeSessionFactory(session))

    recovered = await repository.release_stale_processing(timedelta(minutes=2))

    assert recovered == 2
    lock_sql = str(session.scalar_statements[0].compile(dialect=postgresql.dialect()))
    recovery_sql = str(session.executed_statements[0].compile(dialect=postgresql.dialect()))
    assert "pg_try_advisory_xact_lock" in lock_sql
    assert "tool_invocation.status =" in recovery_sql
    assert "tool_invocation.updated_at <=" in recovery_sql
    assert "tool_invocation.version +" in recovery_sql


@pytest.mark.asyncio
async def test_postgres_admin_recovery_does_not_update_without_exclusive_lock():
    session = FakeSession(scalar_results=[False])
    repository = PostgresToolInvocationRepository(FakeSessionFactory(session))

    with pytest.raises(RuntimeError, match="already running"):
        await repository.release_stale_processing(timedelta(minutes=2))

    assert session.executed_statements == []


@pytest.mark.asyncio
async def test_postgres_processing_guard_holds_shared_lock_until_released():
    connection = FakeConnection()
    repository = PostgresToolInvocationRepository(db_engine=FakeEngine(connection))

    guard = await repository.acquire_processing_guard()
    await repository.release_processing_guard(guard)

    sql = [str(statement.compile(dialect=postgresql.dialect())) for statement in connection.statements]
    assert "pg_advisory_lock_shared" in sql[0]
    assert "pg_advisory_unlock_shared" in sql[1]
    assert connection.closed is True
