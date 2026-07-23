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
