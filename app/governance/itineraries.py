"""正式行程保存治理：首次保存和覆盖均需要审批。"""

from __future__ import annotations

from typing import Protocol

from app.governance.approvals import ApprovalService


class ItineraryRepository(Protocol):
    async def save(self, user_id: str, conversation_id: str, title: str, content: dict) -> dict: ...
    async def get(self, user_id: str, conversation_id: str) -> dict | None: ...


class InMemoryItineraryRepository:
    def __init__(self):
        self.records: dict[tuple[str, str], dict] = {}

    async def save(self, user_id: str, conversation_id: str, title: str, content: dict) -> dict:
        key = (user_id, conversation_id)
        version = self.records.get(key, {}).get("version", 0) + 1
        record = {"user_id": user_id, "conversation_id": conversation_id, "title": title, "content": content, "version": version}
        self.records[key] = record
        return dict(record)

    async def get(self, user_id: str, conversation_id: str) -> dict | None:
        record = self.records.get((user_id, conversation_id))
        return dict(record) if record else None


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
