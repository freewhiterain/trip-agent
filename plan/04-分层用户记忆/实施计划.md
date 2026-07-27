# 用户长期记忆分层架构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让已确认的用户长期记忆（偏好画像 + 行程历史）真正影响规划流程的默认值，同时把偏好写入语义从"按 key 覆盖"改为"只增不覆盖"，并清理被取代的死代码。

**Architecture:** 在现有 `app/memory/`（偏好治理）和 `app/governance/`（行程治理）基础上新增两个纯逻辑模块（偏好默认值解析、行程历史提取），把 `UserPreference` 表的写入语义从 upsert 改为 append-only，在 `POST /tasks` 和行程保存审批通过时分别接入读取和写入，最后删除已被取代、零调用方的 `app/core/store.py::UserMemoryService` 和 `app/core/memory_models.py`。

**Tech Stack:** FastAPI、SQLAlchemy 2.0（async）、Pydantic v2、pytest + pytest-asyncio。

## Global Constraints

- 当次请求里显式填写的 `TravelRequirement` 字段永远优先，长期记忆只填补空字段，绝不覆盖。
- 长期记忆的写入必须经过既有审批流程（`ApprovalService`），本计划不新增绕过审批的写入路径。
- 偏好和行程历史的存储写入语义是 append-only：永远 `INSERT`，永远不物理覆盖已有行；显式删除（`memory.delete`）例外，仍是物理删除，语义是"用户要求遗忘"而不是"新值取代旧值"。
- 任何记忆读取/写入失败都必须优雅降级，不能让"创建规划任务"或"保存正式行程"这两个主流程失败。
- 本项目没有 Alembic 等迁移工具，表结构变更直接改 SQLAlchemy 模型，通过 `Base.metadata.create_all`（`scripts/init_db.py`）在新数据库上生效，与现有其它模型变更方式一致。
- 涉及真实 PostgreSQL 的测试遵循现有约定：标记 `pytest.mark.external` + `skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", ...)`。
- 参考设计文档：`docs/superpowers/specs/2026-07-24-layered-user-memory-design.md`。

---

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

### Task 2: 偏好默认值解析与合并

**Files:**
- Create: `app/memory/defaults.py`
- Test: `tests/test_preference_defaults.py`

**Interfaces:**
- Consumes: Task 1 的 `PreferenceRepository` 协议（`app/memory/service.py` 中定义）——`.list(user_id: str) -> list[PreferenceRecord]`；`TravelRequirement`（`app/schemas/planning.py`）。
- Produces: `LIST_PREFERENCE_KEYS: tuple[str, ...]`、`SCALAR_PREFERENCE_KEYS: tuple[str, ...]`、`async def resolve_preference_defaults(user_id: str, repository: PreferenceRepository) -> dict[str, Any]`、`def apply_preference_defaults(requirement: TravelRequirement, defaults: dict[str, Any]) -> TravelRequirement`——供 Task 5 使用。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_preference_defaults.py
from datetime import date

import pytest

from app.memory.defaults import apply_preference_defaults, resolve_preference_defaults
from app.memory.service import InMemoryPreferenceRepository
from app.schemas.governance import PreferenceRecord
from app.schemas.planning import TravelRequirement


def _requirement(**overrides):
    base = dict(destination="成都", departure_date=date(2026, 8, 1), days=3)
    base.update(overrides)
    return TravelRequirement(**base)


@pytest.mark.asyncio
async def test_resolve_preference_defaults_picks_latest_value_per_key():
    repo = InMemoryPreferenceRepository()
    await repo.upsert(PreferenceRecord(user_id="u1", key="food_preferences", value=["清淡"]))
    await repo.upsert(PreferenceRecord(user_id="u1", key="food_preferences", value=["清淡", "不吃辣"]))

    defaults = await resolve_preference_defaults("u1", repo)

    assert defaults["food_preferences"] == ["清淡", "不吃辣"]


@pytest.mark.asyncio
async def test_resolve_preference_defaults_ignores_keys_outside_vocabulary():
    repo = InMemoryPreferenceRepository()
    await repo.upsert(PreferenceRecord(user_id="u1", key="favorite_color", value="blue"))

    defaults = await resolve_preference_defaults("u1", repo)

    assert defaults == {}


@pytest.mark.asyncio
async def test_resolve_preference_defaults_ignores_type_mismatched_values():
    repo = InMemoryPreferenceRepository()
    await repo.upsert(PreferenceRecord(user_id="u1", key="food_preferences", value="清淡"))
    await repo.upsert(PreferenceRecord(user_id="u1", key="budget", value="很多钱"))

    defaults = await resolve_preference_defaults("u1", repo)

    assert defaults == {}


@pytest.mark.asyncio
async def test_resolve_preference_defaults_accepts_valid_budget_number():
    repo = InMemoryPreferenceRepository()
    await repo.upsert(PreferenceRecord(user_id="u1", key="budget", value=3000))

    defaults = await resolve_preference_defaults("u1", repo)

    assert defaults["budget"] == 3000.0


def test_apply_preference_defaults_fills_only_empty_fields():
    requirement = _requirement(food_preferences=["微辣"])
    defaults = {
        "food_preferences": ["清淡", "不吃辣"],
        "accommodation_preferences": ["经济型"],
        "budget": 3000.0,
    }

    result = apply_preference_defaults(requirement, defaults)

    assert result.food_preferences == ["微辣"]
    assert result.accommodation_preferences == ["经济型"]
    assert result.budget == 3000.0


def test_apply_preference_defaults_never_overrides_explicit_budget():
    requirement = _requirement(budget=1000)
    defaults = {"budget": 5000.0}

    result = apply_preference_defaults(requirement, defaults)

    assert result.budget == 1000


def test_apply_preference_defaults_returns_equivalent_requirement_when_no_defaults_apply():
    requirement = _requirement()

    result = apply_preference_defaults(requirement, {})

    assert result == requirement
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/test_preference_defaults.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.memory.defaults'`

- [ ] **Step 3: 创建 `app/memory/defaults.py`**

```python
"""长期偏好默认值解析：把已确认的偏好画像映射为规划请求的默认值。"""

from __future__ import annotations

from typing import Any

from app.memory.service import PreferenceRepository
from app.schemas.planning import TravelRequirement
from app.utils.logger import app_logger

LIST_PREFERENCE_KEYS = (
    "styles",
    "food_preferences",
    "accommodation_preferences",
    "transport_preferences",
    "special_needs",
)
SCALAR_PREFERENCE_KEYS = ("budget",)


async def resolve_preference_defaults(user_id: str, repository: PreferenceRepository) -> dict[str, Any]:
    """读取该用户已确认的偏好，按 key 取确认时间最新的一条，过滤词表外/类型不匹配的记录。"""
    records = await repository.list(user_id)
    valid_keys = set(LIST_PREFERENCE_KEYS) | set(SCALAR_PREFERENCE_KEYS)

    latest_value_by_key: dict[str, Any] = {}
    for record in sorted(records, key=lambda item: item.confirmed_at):
        if record.key in valid_keys:
            latest_value_by_key[record.key] = record.value

    defaults: dict[str, Any] = {}
    for key, value in latest_value_by_key.items():
        if key in LIST_PREFERENCE_KEYS:
            if isinstance(value, list) and all(isinstance(item, str) for item in value):
                defaults[key] = value
            else:
                app_logger.warning(f"忽略类型不匹配的长期偏好: user={user_id} key={key} value={value!r}")
        else:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                defaults[key] = float(value)
            else:
                app_logger.warning(f"忽略类型不匹配的长期偏好: user={user_id} key={key} value={value!r}")
    return defaults


def apply_preference_defaults(requirement: TravelRequirement, defaults: dict[str, Any]) -> TravelRequirement:
    """只填充 requirement 里为空的字段，已有内容一律不动。返回一个新的 TravelRequirement。"""
    updates: dict[str, Any] = {}
    for key in LIST_PREFERENCE_KEYS:
        if key in defaults and not getattr(requirement, key):
            updates[key] = defaults[key]
    if "budget" in defaults and requirement.budget is None:
        updates["budget"] = defaults["budget"]
    if not updates:
        return requirement
    return requirement.model_copy(update=updates)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_preference_defaults.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: 提交**

```bash
git add app/memory/defaults.py tests/test_preference_defaults.py
git commit -m "feat(memory): resolve confirmed preferences into planning defaults"
```

---

### Task 3: 行程历史记录（Layer 2b）

**Files:**
- Modify: `app/models/governance.py`
- Modify: `app/models/__init__.py`
- Modify: `app/schemas/governance.py`
- Modify: `app/governance/postgres.py`
- Create: `app/memory/trip_history.py`
- Test: `tests/test_trip_history.py`
- Test: `tests/test_preference_and_trip_history_postgres.py`

**Interfaces:**
- Consumes: `SavedItinerary`/`TripHistory` 的落地约定——`content` 字典形如
  `TravelPlanDraft.model_dump(mode="json")` 的结构（含 `requirement.destination`/
  `requirement.departure_date`/`requirement.days` 和 `itinerary[].slots[].period`/
  `.title`），字段缺失时安全跳过，不报错。
- Produces: `TripHistoryRecord`（`app/schemas/governance.py`）、
  `TripHistoryRepository` 协议、`InMemoryTripHistoryRepository`、
  `PostgresTripHistoryRepository`、
  `build_trip_history_record(user_id: str, source_itinerary_id: str, content: dict) -> TripHistoryRecord | None`、
  `async def record_trip_history_from_itinerary(user_id: str, source_itinerary_id: str, content: dict, repository: TripHistoryRepository) -> TripHistoryRecord | None`
  ——供 Task 4 使用。

- [ ] **Step 1: 写失败测试（纯逻辑部分，先不依赖数据库）**

```python
# tests/test_trip_history.py
from datetime import date

import pytest

from app.memory.trip_history import (
    InMemoryTripHistoryRepository,
    build_trip_history_record,
    record_trip_history_from_itinerary,
)


def _content(**overrides):
    base = {
        "requirement": {"destination": "成都", "departure_date": "2026-08-01", "days": 3},
        "itinerary": [
            {
                "day": 1,
                "date": "2026-08-01",
                "slots": [
                    {"period": "morning", "title": "熊猫基地", "description": ""},
                    {"period": "evening", "title": "锦里", "description": ""},
                ],
            },
            {
                "day": 2,
                "date": "2026-08-02",
                "slots": [
                    {"period": "morning", "title": "宽窄巷子", "description": ""},
                ],
            },
        ],
    }
    base.update(overrides)
    return base


def test_build_trip_history_record_extracts_destination_dates_and_attractions():
    record = build_trip_history_record("u1", "itin-1", _content())

    assert record is not None
    assert record.destination == "成都"
    assert record.start_date == date(2026, 8, 1)
    assert record.end_date == date(2026, 8, 3)
    assert record.visited_attractions == ["熊猫基地", "宽窄巷子"]
    assert record.source_itinerary_id == "itin-1"


def test_build_trip_history_record_returns_none_when_requirement_missing():
    assert build_trip_history_record("u1", "itin-1", {"itinerary": []}) is None


def test_build_trip_history_record_returns_none_when_destination_missing():
    content = _content(requirement={"departure_date": "2026-08-01", "days": 3})
    assert build_trip_history_record("u1", "itin-1", content) is None


def test_build_trip_history_record_tolerates_missing_itinerary_section():
    content = {"requirement": {"destination": "西安", "departure_date": "2026-09-01", "days": 2}}

    record = build_trip_history_record("u1", "itin-2", content)

    assert record is not None
    assert record.visited_attractions == []


@pytest.mark.asyncio
async def test_record_trip_history_from_itinerary_appends_to_repository():
    repository = InMemoryTripHistoryRepository()

    result = await record_trip_history_from_itinerary("u1", "itin-1", _content(), repository)

    assert result is not None
    stored = await repository.list("u1")
    assert len(stored) == 1
    assert stored[0].destination == "成都"


@pytest.mark.asyncio
async def test_record_trip_history_from_itinerary_degrades_to_none_on_malformed_content():
    repository = InMemoryTripHistoryRepository()

    result = await record_trip_history_from_itinerary("u1", "itin-1", {"not": "valid"}, repository)

    assert result is None
    assert await repository.list("u1") == []
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/test_trip_history.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.memory.trip_history'`

- [ ] **Step 3: 在 `app/schemas/governance.py` 新增 `TripHistoryRecord`**

把顶部 import：

```python
from datetime import datetime, timezone
```

改为：

```python
from datetime import date, datetime, timezone
```

在 `PreferenceRecord` 类之后（`class ApprovalDecisionRequest` 之前）新增：

```python
class TripHistoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    destination: str
    start_date: date
    end_date: date
    visited_attractions: list[str] = Field(default_factory=list)
    source_itinerary_id: str
    confirmed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 4: 在 `app/models/governance.py` 新增 `TripHistory` ORM 模型**

把顶部 import：

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
```

改为：

```python
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func
```

在文件末尾（`SavedItinerary` 类之后）新增：

```python
class TripHistory(Base):
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), index=True)
    destination: Mapped[str] = mapped_column(String(80))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    visited_attractions: Mapped[list] = mapped_column(JSON, default=list)
    source_itinerary_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("saveditinerary.id", ondelete="CASCADE"), index=True
    )
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), index=True)
```

- [ ] **Step 5: 在 `app/models/__init__.py` 注册新模型**

把：

```python
from app.models.governance import Approval, SavedItinerary, TaskEvent, UserPreference
```

改为：

```python
from app.models.governance import Approval, SavedItinerary, TaskEvent, TripHistory, UserPreference
```

把 `__all__` 列表：

```python
__all__ = [
    "Approval",
    "Base",
    "Conversation",
    "KnowledgeEntity",
    "KnowledgeRelation",
    "Message",
    "SavedItinerary",
    "TaskEvent",
    "ToolInvocation",
    "TripDraft",
    "User",
    "UserPreference",
]
```

改为：

```python
__all__ = [
    "Approval",
    "Base",
    "Conversation",
    "KnowledgeEntity",
    "KnowledgeRelation",
    "Message",
    "SavedItinerary",
    "TaskEvent",
    "ToolInvocation",
    "TripDraft",
    "TripHistory",
    "User",
    "UserPreference",
]
```

- [ ] **Step 6: 创建 `app/memory/trip_history.py`**

```python
"""行程历史记录：用户确认保存正式行程后追加的 Layer 2b 记忆。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Protocol

from app.schemas.governance import TripHistoryRecord
from app.utils.logger import app_logger


class TripHistoryRepository(Protocol):
    async def append(self, record: TripHistoryRecord) -> TripHistoryRecord: ...
    async def list(self, user_id: str) -> list[TripHistoryRecord]: ...


class InMemoryTripHistoryRepository:
    def __init__(self):
        self.records: list[TripHistoryRecord] = []

    async def append(self, record: TripHistoryRecord) -> TripHistoryRecord:
        stored = record.model_copy(deep=True)
        self.records.append(stored)
        return stored.model_copy(deep=True)

    async def list(self, user_id: str) -> list[TripHistoryRecord]:
        return [record.model_copy(deep=True) for record in self.records if record.user_id == user_id]


def _extract_visited_attractions(content: dict[str, Any]) -> list[str]:
    attractions: list[str] = []
    for day in content.get("itinerary", []) or []:
        for slot in day.get("slots", []) or []:
            if slot.get("period") == "morning" and slot.get("title"):
                attractions.append(slot["title"])
    return attractions


def build_trip_history_record(
    user_id: str, source_itinerary_id: str, content: dict[str, Any]
) -> TripHistoryRecord | None:
    """从已保存的行程内容里提取行程历史；字段缺失或格式不符时返回 None，不编造数据。"""
    requirement = content.get("requirement")
    if not isinstance(requirement, dict):
        return None
    destination = requirement.get("destination")
    start_date_raw = requirement.get("departure_date")
    days = requirement.get("days")
    if not destination or not start_date_raw or not isinstance(days, int) or days < 1:
        return None
    try:
        start_date = date.fromisoformat(start_date_raw)
    except (TypeError, ValueError):
        return None
    end_date = start_date + timedelta(days=days - 1)
    return TripHistoryRecord(
        user_id=user_id,
        destination=destination,
        start_date=start_date,
        end_date=end_date,
        visited_attractions=_extract_visited_attractions(content),
        source_itinerary_id=source_itinerary_id,
    )


async def record_trip_history_from_itinerary(
    user_id: str, source_itinerary_id: str, content: dict[str, Any], repository: TripHistoryRepository
) -> TripHistoryRecord | None:
    """构建并追加行程历史；任何异常都只记录 warning，不向上抛出——这是保存行程这个
    主动作的次要副作用，不能因为它失败而拖垮行程保存本身。"""
    try:
        record = build_trip_history_record(user_id, source_itinerary_id, content)
        if record is None:
            app_logger.warning(f"行程内容缺少必要字段，跳过行程历史记录: user={user_id}")
            return None
        return await repository.append(record)
    except Exception as exc:
        app_logger.warning(f"追加行程历史记录失败: user={user_id} error={exc}")
        return None
```

- [ ] **Step 7: 运行测试，确认通过**

Run: `python -m pytest tests/test_trip_history.py -v`
Expected: PASS（6 passed）

- [ ] **Step 8: 在 `app/governance/postgres.py` 新增 `PostgresTripHistoryRepository`**

把顶部 import：

```python
from app.models.governance import Approval, SavedItinerary, TaskEvent, UserPreference
from app.schemas.governance import ApprovalRecord, PreferenceRecord, TaskEventRecord
```

改为：

```python
from app.models.governance import Approval, SavedItinerary, TaskEvent, TripHistory, UserPreference
from app.schemas.governance import ApprovalRecord, PreferenceRecord, TaskEventRecord, TripHistoryRecord
```

在文件末尾新增：

```python
class PostgresTripHistoryRepository:
    def __init__(self, session_factory=async_session_maker):
        self.session_factory = session_factory

    async def append(self, record: TripHistoryRecord) -> TripHistoryRecord:
        entity = TripHistory(
            id=UUID(record.id), user_id=UUID(record.user_id), destination=record.destination,
            start_date=record.start_date, end_date=record.end_date,
            visited_attractions=record.visited_attractions,
            source_itinerary_id=UUID(record.source_itinerary_id),
        )
        async with self.session_factory() as session, session.begin():
            session.add(entity)
            return TripHistoryRecord(
                id=str(entity.id), user_id=str(entity.user_id), destination=entity.destination,
                start_date=entity.start_date, end_date=entity.end_date,
                visited_attractions=entity.visited_attractions,
                source_itinerary_id=str(entity.source_itinerary_id), confirmed_at=entity.confirmed_at,
            )

    async def list(self, user_id: str) -> list[TripHistoryRecord]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(TripHistory).where(TripHistory.user_id == UUID(user_id)).order_by(TripHistory.confirmed_at)
            )
            return [
                TripHistoryRecord(
                    id=str(item.id), user_id=str(item.user_id), destination=item.destination,
                    start_date=item.start_date, end_date=item.end_date,
                    visited_attractions=item.visited_attractions,
                    source_itinerary_id=str(item.source_itinerary_id), confirmed_at=item.confirmed_at,
                )
                for item in result.scalars()
            ]
```

- [ ] **Step 9: 写真实数据库的 opt-in 测试**

```python
# tests/test_preference_and_trip_history_postgres.py
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
```

Run（需要本地已启动 PostgreSQL，跟 README 的 Docker 启动步骤一致）：
`RUN_POSTGRES_TESTS=1 python -m pytest tests/test_preference_and_trip_history_postgres.py -v`
Expected: PASS（2 passed）。如果本地没有启动 PostgreSQL，跳过此步骤即可——
默认不设置 `RUN_POSTGRES_TESTS` 时这两个测试会被自动跳过，不影响其它任务。

- [ ] **Step 10: 提交**

```bash
git add app/models/governance.py app/models/__init__.py app/schemas/governance.py app/governance/postgres.py app/memory/trip_history.py tests/test_trip_history.py tests/test_preference_and_trip_history_postgres.py
git commit -m "feat(memory): add trip-history record model, repository, and extraction"
```

---

### Task 4: 把行程历史接入 `ItineraryGovernanceService`

**Files:**
- Modify: `app/governance/itineraries.py`
- Test: `tests/test_itinerary_trip_history_wiring.py`

**Interfaces:**
- Consumes: Task 3 的 `TripHistoryRepository`、`record_trip_history_from_itinerary`（`app/memory/trip_history.py`）。
- Produces: `ItineraryGovernanceService.__init__(approvals, repository, trip_history: TripHistoryRepository | None = None)`——第三个参数可选，向后兼容——供 Task 5 使用。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_itinerary_trip_history_wiring.py
import pytest

from app.governance.approvals import ApprovalService, InMemoryApprovalRepository
from app.governance.itineraries import InMemoryItineraryRepository, ItineraryGovernanceService
from app.memory.trip_history import InMemoryTripHistoryRepository


def _content():
    return {
        "requirement": {"destination": "成都", "departure_date": "2026-08-01", "days": 3},
        "itinerary": [
            {
                "day": 1,
                "date": "2026-08-01",
                "slots": [{"period": "morning", "title": "熊猫基地", "description": ""}],
            }
        ],
    }


@pytest.mark.asyncio
async def test_confirmed_itinerary_save_appends_trip_history():
    approvals = ApprovalService(InMemoryApprovalRepository())
    itineraries = InMemoryItineraryRepository()
    trip_history = InMemoryTripHistoryRepository()
    service = ItineraryGovernanceService(approvals, itineraries, trip_history)
    request = await service.request_save("t", "u", "c", "成都行程", _content())
    await approvals.decide(request.id, "u", "approve")

    await service.apply(request.id, "u")

    history = await trip_history.list("u")
    assert len(history) == 1
    assert history[0].destination == "成都"
    assert history[0].visited_attractions == ["熊猫基地"]


@pytest.mark.asyncio
async def test_itinerary_save_succeeds_even_when_trip_history_repository_fails():
    class FailingTripHistoryRepository:
        async def append(self, record):
            raise RuntimeError("db unavailable")

        async def list(self, user_id):
            return []

    approvals = ApprovalService(InMemoryApprovalRepository())
    itineraries = InMemoryItineraryRepository()
    service = ItineraryGovernanceService(approvals, itineraries, FailingTripHistoryRepository())
    request = await service.request_save("t", "u", "c", "成都行程", _content())
    await approvals.decide(request.id, "u", "approve")

    saved = await service.apply(request.id, "u")

    assert saved["version"] == 1


@pytest.mark.asyncio
async def test_itinerary_save_works_without_trip_history_repository_configured():
    approvals = ApprovalService(InMemoryApprovalRepository())
    itineraries = InMemoryItineraryRepository()
    service = ItineraryGovernanceService(approvals, itineraries)
    request = await service.request_save("t", "u", "c", "成都行程", _content())
    await approvals.decide(request.id, "u", "approve")

    saved = await service.apply(request.id, "u")

    assert saved["version"] == 1
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/test_itinerary_trip_history_wiring.py -v`
Expected: FAIL —— `TypeError: ItineraryGovernanceService(...) takes 3 positional arguments but 4 were given`
（当前 `__init__` 只接受 `approvals`/`repository` 两个参数）

- [ ] **Step 3: 修改 `app/governance/itineraries.py`**

把顶部 import：

```python
from __future__ import annotations

from typing import Protocol

from app.governance.approvals import ApprovalService
```

改为：

```python
from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from app.governance.approvals import ApprovalService
from app.memory.trip_history import TripHistoryRepository, record_trip_history_from_itinerary
```

把 `InMemoryItineraryRepository.save`：

```python
    async def save(self, user_id: str, conversation_id: str, title: str, content: dict) -> dict:
        key = (user_id, conversation_id)
        version = self.records.get(key, {}).get("version", 0) + 1
        record = {"user_id": user_id, "conversation_id": conversation_id, "title": title, "content": content, "version": version}
        self.records[key] = record
        return dict(record)
```

改为（补上 `id` 字段，和 `PostgresItineraryRepository.save` 的返回结构保持一致，供行程历史提取使用）：

```python
    async def save(self, user_id: str, conversation_id: str, title: str, content: dict) -> dict:
        key = (user_id, conversation_id)
        version = self.records.get(key, {}).get("version", 0) + 1
        record = {
            "id": str(uuid4()),
            "user_id": user_id,
            "conversation_id": conversation_id,
            "title": title,
            "content": content,
            "version": version,
        }
        self.records[key] = record
        return dict(record)
```

把 `ItineraryGovernanceService`：

```python
class ItineraryGovernanceService:
    def __init__(self, approvals: ApprovalService, repository: ItineraryRepository):
        self.approvals = approvals
        self.repository = repository

    async def request_save(self, task_id: str, user_id: str, conversation_id: str, title: str, content: dict):
        action = "itinerary.overwrite" if await self.repository.get(user_id, conversation_id) else "itinerary.save"
        return await self.approvals.request(
            task_id,
            user_id,
            action,
            {"conversation_id": conversation_id, "title": title, "content": content},
        )

    async def apply(self, approval_id: str, user_id: str) -> dict:
        approval = await self.approvals.repository.get(approval_id)
        if approval is None or approval.user_id != user_id:
            raise PermissionError("审批不存在或不属于当前用户")
        if approval.status not in {"approved", "edited"}:
            raise PermissionError("保存或覆盖正式行程必须先获得用户批准")
        if approval.action not in {"itinerary.save", "itinerary.overwrite"}:
            raise ValueError("审批动作不是行程保存")
        payload = approval.decision_payload if approval.status == "edited" else approval.payload
        return await self.repository.save(user_id, payload["conversation_id"], payload["title"], payload["content"])
```

改为：

```python
class ItineraryGovernanceService:
    def __init__(
        self,
        approvals: ApprovalService,
        repository: ItineraryRepository,
        trip_history: TripHistoryRepository | None = None,
    ):
        self.approvals = approvals
        self.repository = repository
        self.trip_history = trip_history

    async def request_save(self, task_id: str, user_id: str, conversation_id: str, title: str, content: dict):
        action = "itinerary.overwrite" if await self.repository.get(user_id, conversation_id) else "itinerary.save"
        return await self.approvals.request(
            task_id,
            user_id,
            action,
            {"conversation_id": conversation_id, "title": title, "content": content},
        )

    async def apply(self, approval_id: str, user_id: str) -> dict:
        approval = await self.approvals.repository.get(approval_id)
        if approval is None or approval.user_id != user_id:
            raise PermissionError("审批不存在或不属于当前用户")
        if approval.status not in {"approved", "edited"}:
            raise PermissionError("保存或覆盖正式行程必须先获得用户批准")
        if approval.action not in {"itinerary.save", "itinerary.overwrite"}:
            raise ValueError("审批动作不是行程保存")
        payload = approval.decision_payload if approval.status == "edited" else approval.payload
        saved = await self.repository.save(user_id, payload["conversation_id"], payload["title"], payload["content"])
        if self.trip_history is not None:
            await record_trip_history_from_itinerary(user_id, str(saved["id"]), saved["content"], self.trip_history)
        return saved
```

- [ ] **Step 4: 运行新测试和既有治理测试，确认通过**

Run: `python -m pytest tests/test_itinerary_trip_history_wiring.py tests/test_phase3_governance.py -v`
Expected: PASS（全部通过——`test_formal_itinerary_save_and_overwrite_are_approved_and_versioned`
不受影响，因为它只检查 `version` 字段，新增的 `id` 字段不影响该断言）

- [ ] **Step 5: 提交**

```bash
git add app/governance/itineraries.py tests/test_itinerary_trip_history_wiring.py
git commit -m "feat(memory): append trip history when a formal itinerary save is approved"
```

---

### Task 5: 接入 API 层

**Files:**
- Modify: `app/api/v1/planning.py`
- Test: `tests/test_task_creation_uses_preference_defaults.py`

**Interfaces:**
- Consumes: Task 2 的 `resolve_preference_defaults`/`apply_preference_defaults`（`app/memory/defaults.py`）；Task 3 的 `PostgresTripHistoryRepository`（`app/governance/postgres.py`）；Task 4 的
  `ItineraryGovernanceService(approvals, repository, trip_history=None)`。
- Produces: `POST /tasks` 在创建规划任务前合并长期偏好默认值；`POST /approvals/{approval_id}/decision` 对
  `itinerary.*` 审批的 apply 调用带上行程历史仓库。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_task_creation_uses_preference_defaults.py
from datetime import date

import pytest

import app.api.v1.planning as planning_api
from app.schemas.governance import ApprovalDecisionRequest, ApprovalRecord, PreferenceRecord
from app.schemas.planning import BudgetSummary, TravelPlanDraft, TravelRequirement


class _FakePreferenceRepository:
    def __init__(self, records):
        self._records = records

    async def list(self, user_id):
        return self._records


class _FailingPreferenceRepository:
    async def list(self, user_id):
        raise RuntimeError("db down")


class _FakeUser:
    def __init__(self, user_id):
        self.id = user_id


async def _noop_coro(value=None):
    return value


def _patch_task_creation_collaborators(monkeypatch, preference_repository, requirement_holder):
    monkeypatch.setattr(planning_api, "PostgresPreferenceRepository", lambda: preference_repository)
    monkeypatch.setattr(planning_api, "get_checkpointer", lambda: _noop_coro(None))
    monkeypatch.setattr(planning_api, "TaskEventService", lambda *args, **kwargs: None)
    monkeypatch.setattr(planning_api, "PostgresEventRepository", lambda: None)

    async def fake_run_travel_planning(requirement, **kwargs):
        requirement_holder["requirement"] = requirement
        return TravelPlanDraft(
            requirement=requirement, itinerary=[], budget=BudgetSummary(), worker_results=[], evidence=[]
        )

    monkeypatch.setattr(planning_api, "run_travel_planning", fake_run_travel_planning)


@pytest.mark.asyncio
async def test_create_planning_task_fills_empty_fields_from_confirmed_preferences(monkeypatch):
    records = [PreferenceRecord(user_id="u1", key="food_preferences", value=["清淡", "不吃辣"])]
    captured = {}
    _patch_task_creation_collaborators(monkeypatch, _FakePreferenceRepository(records), captured)
    requirement = TravelRequirement(destination="成都", departure_date=date(2026, 8, 1), days=3)

    await planning_api.create_planning_task(requirement, _FakeUser("u1"))

    assert captured["requirement"].food_preferences == ["清淡", "不吃辣"]


@pytest.mark.asyncio
async def test_create_planning_task_never_overrides_explicit_field(monkeypatch):
    records = [PreferenceRecord(user_id="u1", key="food_preferences", value=["清淡", "不吃辣"])]
    captured = {}
    _patch_task_creation_collaborators(monkeypatch, _FakePreferenceRepository(records), captured)
    requirement = TravelRequirement(
        destination="成都", departure_date=date(2026, 8, 1), days=3, food_preferences=["微辣"]
    )

    await planning_api.create_planning_task(requirement, _FakeUser("u1"))

    assert captured["requirement"].food_preferences == ["微辣"]


@pytest.mark.asyncio
async def test_create_planning_task_degrades_to_no_defaults_when_preference_lookup_fails(monkeypatch):
    captured = {}
    _patch_task_creation_collaborators(monkeypatch, _FailingPreferenceRepository(), captured)
    requirement = TravelRequirement(destination="成都", departure_date=date(2026, 8, 1), days=3)

    result = await planning_api.create_planning_task(requirement, _FakeUser("u1"))

    assert result["status"] == "completed"
    assert captured["requirement"].food_preferences == []


@pytest.mark.asyncio
async def test_decide_approval_wires_trip_history_repository_into_itinerary_apply(monkeypatch):
    captured_args = {}

    class _SpyItineraryGovernanceService:
        def __init__(self, approvals, repository, trip_history=None):
            captured_args["trip_history"] = trip_history

        async def apply(self, approval_id, user_id):
            return {"id": "itin-1", "version": 1}

    class _FakeApprovalService:
        def __init__(self, repository):
            self.repository = repository

        async def decide(self, approval_id, user_id, decision, payload):
            return ApprovalRecord(
                id=approval_id, task_id="t", user_id=user_id, action="itinerary.save",
                payload={}, status="approved",
            )

    monkeypatch.setattr(planning_api, "ItineraryGovernanceService", _SpyItineraryGovernanceService)
    monkeypatch.setattr(planning_api, "ApprovalService", _FakeApprovalService)
    monkeypatch.setattr(planning_api, "PostgresApprovalRepository", lambda: None)
    monkeypatch.setattr(planning_api, "PostgresItineraryRepository", lambda: None)
    monkeypatch.setattr(planning_api, "PostgresTripHistoryRepository", lambda: "trip-history-repo-instance")

    await planning_api.decide_approval(
        "approval-1", ApprovalDecisionRequest(decision="approve"), _FakeUser("u1")
    )

    assert captured_args["trip_history"] == "trip-history-repo-instance"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/test_task_creation_uses_preference_defaults.py -v`
Expected: FAIL —— `AttributeError: <module 'app.api.v1.planning'> does not have the attribute
'PostgresTripHistoryRepository'`（`decide_approval` 相关测试）以及
`captured["requirement"].food_preferences` 仍是 `[]`（`create_planning_task` 相关测试，因为还没有合并偏好默认值）

- [ ] **Step 3: 修改 `app/api/v1/planning.py`**

把顶部 import：

```python
from app.governance.postgres import (
    PostgresApprovalRepository,
    PostgresEventRepository,
    PostgresItineraryRepository,
    PostgresPreferenceRepository,
)
from app.memory.service import MemoryGovernanceService
```

改为：

```python
from app.governance.postgres import (
    PostgresApprovalRepository,
    PostgresEventRepository,
    PostgresItineraryRepository,
    PostgresPreferenceRepository,
    PostgresTripHistoryRepository,
)
from app.memory.defaults import apply_preference_defaults, resolve_preference_defaults
from app.memory.service import MemoryGovernanceService
from app.utils.logger import app_logger
```

把 `create_planning_task`：

```python
@router.post("/tasks")
async def create_planning_task(requirement: TravelRequirement, user: User = Depends(get_current_user)):
    task_id = uuid4().hex
    event_service = TaskEventService(PostgresEventRepository())
    draft = await run_travel_planning(
        requirement,
        checkpointer=await get_checkpointer(),
        event_service=event_service,
        task_id=task_id,
        user_id=str(user.id),
    )
    return {"task_id": task_id, "status": "completed", "draft": draft.model_dump(mode="json")}
```

改为：

```python
@router.post("/tasks")
async def create_planning_task(requirement: TravelRequirement, user: User = Depends(get_current_user)):
    task_id = uuid4().hex
    event_service = TaskEventService(PostgresEventRepository())
    try:
        defaults = await resolve_preference_defaults(str(user.id), PostgresPreferenceRepository())
    except Exception as exc:
        app_logger.warning(f"读取长期偏好失败，按无偏好处理: task_id={task_id} error={exc}")
        defaults = {}
    requirement = apply_preference_defaults(requirement, defaults)
    draft = await run_travel_planning(
        requirement,
        checkpointer=await get_checkpointer(),
        event_service=event_service,
        task_id=task_id,
        user_id=str(user.id),
    )
    return {"task_id": task_id, "status": "completed", "draft": draft.model_dump(mode="json")}
```

把 `decide_approval` 里的 itinerary 分支：

```python
        elif record.status in {"approved", "edited"} and record.action.startswith("itinerary."):
            applied = await ItineraryGovernanceService(
                approvals, PostgresItineraryRepository()
            ).apply(record.id, str(user.id))
```

改为：

```python
        elif record.status in {"approved", "edited"} and record.action.startswith("itinerary."):
            applied = await ItineraryGovernanceService(
                approvals, PostgresItineraryRepository(), PostgresTripHistoryRepository()
            ).apply(record.id, str(user.id))
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_task_creation_uses_preference_defaults.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 运行全部治理相关测试，确认没有回归**

Run: `python -m pytest tests/test_phase3_governance.py tests/test_phase4_api_and_sse.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add app/api/v1/planning.py tests/test_task_creation_uses_preference_defaults.py
git commit -m "feat(memory): wire confirmed preference defaults and trip-history repository into planning API"
```

---

### Task 6: 清理死代码

**Files:**
- Delete: `app/core/memory_models.py`
- Modify: `app/core/store.py`
- Test: 无新增测试文件；用全量回归验证

**Interfaces:**
- Consumes: 无
- Produces: 无（纯删除，`StoreManager`/`get_store`/`store_lifespan` 的对外接口不变）

- [ ] **Step 1: 确认没有遗漏的引用**

Run: `python -c "import subprocess; print(subprocess.run(['git', 'grep', '-n', 'UserMemoryService\\|memory_models\\|get_user_memory_service'], capture_output=True, text=True).stdout)"`
Expected: 只输出 `app/core/store.py` 自身的定义行（`class UserMemoryService`、
`def get_user_memory_service`）和 `app/core/memory_models.py` 的内容——没有
其它文件引用它们。如果发现了别的引用，先停下来检查那个引用是否也是死代码，
不要直接删。

- [ ] **Step 2: 删除 `app/core/memory_models.py`**

```bash
git rm app/core/memory_models.py
```

- [ ] **Step 3: 重写 `app/core/store.py`，去掉 `UserMemoryService` 和相关 import**

把整个文件内容替换为：

```python
"""
PostgreSQL Store 配置
长期记忆（用户级数据持久化）
"""
import asyncio
from typing import Optional
from contextlib import asynccontextmanager
from langgraph.store.postgres import AsyncPostgresStore
from psycopg_pool import AsyncConnectionPool

from app.config import settings
from app.utils.logger import app_logger


class StoreManager:
    """Store 管理器（单例模式）"""

    _instance: Optional['StoreManager'] = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.store: Optional[AsyncPostgresStore] = None
        self.pool: Optional[AsyncConnectionPool] = None

    @classmethod
    async def get_instance(cls) -> 'StoreManager':
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    await cls._instance.initialize()
        return cls._instance

    async def initialize(self):
        if self.store is not None:
            app_logger.warning("Store 已初始化，跳过")
            return

        try:
            app_logger.info("初始化 PostgreSQL Store...")

            self.pool = AsyncConnectionPool(
                conninfo=settings.database_url,
                min_size=2,
                max_size=20,
                timeout=30,
                kwargs={"autocommit": True}
            )
            await self.pool.open()

            self.store = AsyncPostgresStore(self.pool)

            app_logger.info("✅ Store 初始化完成")

        except Exception as e:
            app_logger.error(f"❌ Store 初始化失败: {e}")
            raise

    async def close(self):
        if self.pool:
            await self.pool.close()
            app_logger.info("Connection Pool 已关闭")

    def get_store(self) -> AsyncPostgresStore:
        if self.store is None:
            raise RuntimeError("Store 未初始化，请先调用 initialize()")
        return self.store


async def get_store() -> AsyncPostgresStore:
    manager = await StoreManager.get_instance()
    return manager.get_store()


@asynccontextmanager
async def store_lifespan():
    manager = await StoreManager.get_instance()
    try:
        yield manager.get_store()
    finally:
        await manager.close()
```

- [ ] **Step 4: 确认应用仍能正常导入和启动依赖**

Run: `python -c "from app.core.store import StoreManager, get_store, store_lifespan; from app.main import app; print('ok')"`
Expected: 输出 `ok`，无 `ImportError`/`ModuleNotFoundError`

- [ ] **Step 5: 跑全量回归测试**

Run: `python -m pytest -q`
Expected: 全部 PASS（除 `external`/`RUN_POSTGRES_TESTS`/`RUN_OLLAMA_TESTS` 等
opt-in 测试按现有约定被跳过之外），没有因为删除死代码导致的 import 报错。

- [ ] **Step 6: 提交**

```bash
git add -A app/core/store.py app/core/memory_models.py
git commit -m "chore(memory): remove superseded UserMemoryService and memory_models dead code"
```

---

## 完成后验收清单

- [ ] `POST /tasks` 创建规划任务时，用户已确认的偏好会填充当次请求里为空的字段，已填写的字段不受影响。
- [ ] 偏好写入是 append-only：同一 `key` 多次确认会保留全部历史记录，读取时取最新一条。
- [ ] `itinerary.save`/`itinerary.overwrite` 审批通过后会产生一条可查询的行程历史记录；行程历史写入失败不影响行程保存本身。
- [ ] `app/core/memory_models.py` 已删除，`app/core/store.py` 只保留 `StoreManager`/`get_store`/`store_lifespan`。
- [ ] `python -m pytest -q` 全绿（opt-in 测试按约定跳过）。
