"""经审批后才写入的精确偏好与语义记忆服务。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

from app.governance.approvals import ApprovalService
from app.memory.mem0_client import NullSemanticMemory, SemanticMemory
from app.schemas.governance import ApprovalRecord, PreferenceRecord


class PreferenceRepository(Protocol):
    async def upsert(self, record: PreferenceRecord) -> PreferenceRecord: ...
    async def delete(self, user_id: str, key: str) -> bool: ...
    async def list(self, user_id: str) -> list[PreferenceRecord]: ...


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


class MemoryGovernanceService:
    ALLOWED_ACTIONS = {"memory.upsert", "memory.delete"}

    def __init__(
        self,
        approvals: ApprovalService,
        preferences: PreferenceRepository,
        semantic: SemanticMemory | None = None,
    ):
        self.approvals = approvals
        self.preferences = preferences
        self.semantic = semantic or NullSemanticMemory()

    async def request_upsert(self, task_id: str, user_id: str, key: str, value: Any) -> ApprovalRecord:
        return await self.approvals.request(task_id, user_id, "memory.upsert", {"key": key, "value": value})

    async def request_delete(self, task_id: str, user_id: str, key: str) -> ApprovalRecord:
        return await self.approvals.request(task_id, user_id, "memory.delete", {"key": key})

    async def apply(self, approval_id: str, user_id: str) -> PreferenceRecord | bool:
        approval = await self.approvals.repository.get(approval_id)
        if approval is None or approval.user_id != user_id:
            raise PermissionError("审批不存在或不属于当前用户")
        if approval.status not in {"approved", "edited"}:
            raise PermissionError("长期记忆写入必须先获得用户批准")
        if approval.action not in self.ALLOWED_ACTIONS:
            raise ValueError("审批动作不是记忆操作")
        payload = approval.decision_payload if approval.status == "edited" else approval.payload
        if approval.action == "memory.delete":
            return await self.preferences.delete(user_id, str(payload["key"]))

        record = await self.preferences.upsert(
            PreferenceRecord(user_id=user_id, key=str(payload["key"]), value=payload["value"])
        )
        await self.semantic.add_confirmed(
            user_id,
            f"用户确认偏好 {record.key}: {record.value}",
            {"preference_key": record.key},
        )
        return record
