import pytest

from app.memory.service import InMemoryPreferenceRepository
from app.schemas.governance import PreferenceRecord


@pytest.mark.asyncio
async def test_upsert_appends_new_record_instead_of_overwriting():
    repo = InMemoryPreferenceRepository()
    await repo.upsert(PreferenceRecord(user_id="u1", key="food_preferences", value=["清淡"]))
    await repo.upsert(PreferenceRecord(user_id="u1", key="food_preferences", value=["清淡", "不吃辣"]))

    records = await repo.list("u1")

    assert len(records) == 2
    assert records[0].value == ["清淡"]
    assert records[1].value == ["清淡", "不吃辣"]


@pytest.mark.asyncio
async def test_delete_removes_all_historical_records_for_key():
    repo = InMemoryPreferenceRepository()
    await repo.upsert(PreferenceRecord(user_id="u1", key="budget", value=300))
    await repo.upsert(PreferenceRecord(user_id="u1", key="budget", value=500))

    deleted = await repo.delete("u1", "budget")

    assert deleted is True
    assert await repo.list("u1") == []


@pytest.mark.asyncio
async def test_delete_only_affects_matching_user_and_key():
    repo = InMemoryPreferenceRepository()
    await repo.upsert(PreferenceRecord(user_id="u1", key="budget", value=300))
    await repo.upsert(PreferenceRecord(user_id="u2", key="budget", value=400))

    await repo.delete("u1", "budget")

    assert await repo.list("u1") == []
    assert len(await repo.list("u2")) == 1


@pytest.mark.asyncio
async def test_delete_returns_false_when_nothing_matches():
    repo = InMemoryPreferenceRepository()

    assert await repo.delete("u1", "budget") is False
