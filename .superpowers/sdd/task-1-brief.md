### Task 1: 偏好存储改为 ADD-only

**Files:**
- Modify: `app/models/governance.py`
- Modify: `app/governance/postgres.py`
- Modify: `app/memory/service.py`
- Test: `tests/test_preference_append_only.py`

**Interfaces:**
- Consumes: 无（本任务是最底层的存储语义变更）
- Produces: `InMemoryPreferenceRepository.upsert(record: PreferenceRecord) -> PreferenceRecord`（每次调用追加新记录）、`.delete(user_id: str, key: str) -> bool`（物理删除该 key 全部历史记录）、`.list(user_id: str) -> list[PreferenceRecord]`（返回该用户全部历史记录，按插入顺序）；`PostgresPreferenceRepository` 同一套行为的真实数据库实现；供 Task 2 使用。

- [ ] **Step 1: 写失败测试，验证内存版仓库是 append-only**

```python
# tests/test_preference_append_only.py
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/test_preference_append_only.py -v`
Expected: FAIL —— `test_upsert_appends_new_record_instead_of_overwriting` 里 `len(records) == 2` 会失败，因为当前 `InMemoryPreferenceRepository.upsert` 是按 `(user_id, key)` 覆盖式写入，第二次调用会覆盖第一次，实际长度是 1。

- [ ] **Step 3: 修改 `app/memory/service.py` 的 `InMemoryPreferenceRepository`**

把：

```python
class InMemoryPreferenceRepository:
    def __init__(self):
        self.records: dict[tuple[str, str], PreferenceRecord] = {}

    async def upsert(self, record: PreferenceRecord) -> PreferenceRecord:
        key = (record.user_id, record.key)
        existing = self.records.get(key)
        if existing:
            record.id = existing.id
            record.confirmed_at = existing.confirmed_at
        record.updated_at = datetime.now(timezone.utc)
        self.records[key] = record.model_copy(deep=True)
        return record

    async def delete(self, user_id: str, key: str) -> bool:
        return self.records.pop((user_id, key), None) is not None

    async def list(self, user_id: str) -> list[PreferenceRecord]:
        return [record.model_copy(deep=True) for (owner, _), record in self.records.items() if owner == user_id]
```

改为：

```python
class InMemoryPreferenceRepository:
    def __init__(self):
        self.records: list[PreferenceRecord] = []

    async def upsert(self, record: PreferenceRecord) -> PreferenceRecord:
        now = datetime.now(timezone.utc)
        stored = record.model_copy(deep=True)
        stored.confirmed_at = now
        stored.updated_at = now
        self.records.append(stored)
        return stored.model_copy(deep=True)

    async def delete(self, user_id: str, key: str) -> bool:
        remaining = [r for r in self.records if not (r.user_id == user_id and r.key == key)]
        deleted = len(remaining) != len(self.records)
        self.records = remaining
        return deleted

    async def list(self, user_id: str) -> list[PreferenceRecord]:
        return [record.model_copy(deep=True) for record in self.records if record.user_id == user_id]
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_preference_append_only.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 修改 `app/models/governance.py` 的 `UserPreference`，去掉唯一约束**

把：

```python
class UserPreference(Base):
    __table_args__ = (UniqueConstraint("user_id", "key", name="uq_user_preference_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    value: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(40), default="user_confirmed")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), onupdate=func.now())
```

改为（去掉 `__table_args__`，`key`/`confirmed_at` 加索引以便按 key 取最新一条时高效查询）：

```python
class UserPreference(Base):
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(40), default="user_confirmed")
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
```

（`UniqueConstraint` 的 import 仍被 `TaskEvent`/`SavedItinerary` 使用，不要删除该 import。）

- [ ] **Step 6: 修改 `app/governance/postgres.py` 的 `PostgresPreferenceRepository`**

把文件顶部的 import：

```python
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
```

改为（`insert` 不再需要）：

```python
from sqlalchemy import func, select, text
```

把：

```python
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
```

改为：

```python
class PostgresPreferenceRepository:
    def __init__(self, session_factory=async_session_maker):
        self.session_factory = session_factory

    async def upsert(self, record: PreferenceRecord) -> PreferenceRecord:
        now = datetime.now(timezone.utc)
        entity = UserPreference(
            id=UUID(record.id), user_id=UUID(record.user_id), key=record.key,
            value=record.value, source=record.source,
            confirmed_at=now, updated_at=now,
        )
        async with self.session_factory() as session, session.begin():
            session.add(entity)
            return PreferenceRecord(
                id=str(entity.id), user_id=str(entity.user_id), key=entity.key,
                value=entity.value, source=entity.source,
                confirmed_at=entity.confirmed_at, updated_at=entity.updated_at,
            )

    async def delete(self, user_id: str, key: str) -> bool:
        async with self.session_factory() as session, session.begin():
            entities = (
                await session.scalars(
                    select(UserPreference).where(UserPreference.user_id == UUID(user_id), UserPreference.key == key)
                )
            ).all()
            if not entities:
                return False
            for entity in entities:
                await session.delete(entity)
            return True

    async def list(self, user_id: str) -> list[PreferenceRecord]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(UserPreference)
                .where(UserPreference.user_id == UUID(user_id))
                .order_by(UserPreference.key, UserPreference.confirmed_at)
            )
            return [
                PreferenceRecord(
                    id=str(item.id), user_id=str(item.user_id), key=item.key,
                    value=item.value, source=item.source,
                    confirmed_at=item.confirmed_at, updated_at=item.updated_at,
                )
                for item in result.scalars()
            ]
```

- [ ] **Step 7: 运行既有治理测试，确认没有回归**

Run: `python -m pytest tests/test_phase3_governance.py -v`
Expected: PASS —— `test_memory_is_not_written_before_owner_approval` 和
`test_edit_and_delete_memory_require_separate_approvals` 里都只调用了一次
`service.apply` 写入同一个 key，ADD-only 语义下仍然只产生一条记录，不需要
修改这两个测试的断言。

- [ ] **Step 8: 提交**

```bash
git add app/models/governance.py app/governance/postgres.py app/memory/service.py tests/test_preference_append_only.py
git commit -m "feat(memory): make preference writes append-only instead of overwrite-by-key"
```

---

