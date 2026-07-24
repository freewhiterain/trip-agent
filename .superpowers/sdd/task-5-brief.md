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

