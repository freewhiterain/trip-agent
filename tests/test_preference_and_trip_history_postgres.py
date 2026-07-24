import os
import uuid
from datetime import date

import pytest
from sqlalchemy import delete

import app.models  # noqa: F401
from app.governance.postgres import PostgresPreferenceRepository, PostgresTripHistoryRepository
from app.models.base import async_session_maker, init_db
from app.models.governance import TripHistory, UserPreference
from app.models.user import User
from app.schemas.governance import PreferenceRecord, TripHistoryRecord

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_TESTS") != "1",
        reason="requires RUN_POSTGRES_TESTS=1 and a reachable PostgreSQL database",
    ),
]


@pytest.mark.asyncio
async def test_postgres_preference_upsert_is_add_only_and_delete_removes_all_rows():
    await init_db()
    user_id = uuid.uuid4()
    token = uuid.uuid4().hex[:8]

    try:
        async with async_session_maker() as session, session.begin():
            session.add(
                User(id=user_id, username=f"memtest-{token}", email=f"memtest-{token}@example.test", password_hash="test-only")
            )

        repo = PostgresPreferenceRepository()
        await repo.upsert(PreferenceRecord(user_id=str(user_id), key="food_preferences", value=["清淡"]))
        await repo.upsert(PreferenceRecord(user_id=str(user_id), key="food_preferences", value=["清淡", "不吃辣"]))

        records = await repo.list(str(user_id))
        assert len(records) == 2
        assert records[-1].value == ["清淡", "不吃辣"]

        deleted = await repo.delete(str(user_id), "food_preferences")
        assert deleted is True
        assert await repo.list(str(user_id)) == []
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(UserPreference).where(UserPreference.user_id == user_id))
            await session.execute(delete(User).where(User.id == user_id))


@pytest.mark.asyncio
async def test_postgres_trip_history_append_and_list_round_trip():
    await init_db()
    user_id = uuid.uuid4()
    token = uuid.uuid4().hex[:8]

    try:
        async with async_session_maker() as session, session.begin():
            session.add(
                User(id=user_id, username=f"triptest-{token}", email=f"triptest-{token}@example.test", password_hash="test-only")
            )

        repo = PostgresTripHistoryRepository()
        record = TripHistoryRecord(
            user_id=str(user_id), destination="成都", start_date=date(2026, 8, 1), end_date=date(2026, 8, 3),
            visited_attractions=["熊猫基地"], source_itinerary_id=str(uuid.uuid4()),
        )

        await repo.append(record)
        stored = await repo.list(str(user_id))

        assert len(stored) == 1
        assert stored[0].destination == "成都"
        assert stored[0].visited_attractions == ["熊猫基地"]
    finally:
        async with async_session_maker() as session, session.begin():
            await session.execute(delete(TripHistory).where(TripHistory.user_id == user_id))
            await session.execute(delete(User).where(User.id == user_id))
