"""审批、事件和记忆治理的数据契约。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


ApprovalDecision = Literal["approve", "edit", "reject"]
ApprovalStatus = Literal["pending", "approved", "edited", "rejected"]


class ApprovalRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    user_id: str
    action: str
    payload: dict[str, Any]
    status: ApprovalStatus = "pending"
    decision_payload: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: datetime | None = None


class TaskEventRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    task_id: str
    conversation_id: str | None = None
    user_id: str
    event_type: str
    sequence: int = Field(ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PreferenceRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    key: str
    value: Any
    source: str = "user_confirmed"
    confirmed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TripHistoryRecord(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: str
    destination: str
    start_date: date
    end_date: date
    visited_attractions: list[str] = Field(default_factory=list)
    source_itinerary_id: str
    confirmed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ApprovalDecisionRequest(BaseModel):
    decision: ApprovalDecision
    payload: dict[str, Any] | None = None


class ItinerarySaveRequest(BaseModel):
    task_id: str
    title: str = Field(min_length=1, max_length=200)
    content: dict[str, Any]


class PreferenceProposalRequest(BaseModel):
    task_id: str
    key: str = Field(min_length=1, max_length=80)
    value: Any
