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

