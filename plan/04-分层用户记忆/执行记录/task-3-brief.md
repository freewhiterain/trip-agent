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

