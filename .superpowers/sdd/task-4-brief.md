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

