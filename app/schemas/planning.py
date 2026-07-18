"""多 Agent 旅行规划的公共数据契约。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


TaskType = Literal["destination", "transport", "hotel", "food", "weather"]
WorkerStatus = Literal["completed", "partial", "failed"]


class TravelRequirement(BaseModel):
    """一次国内旅行规划所需的最小结构化需求。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    origin: str = Field(min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    departure_date: date
    days: int = Field(ge=1, le=30)
    adults: int = Field(default=1, ge=1, le=30)
    children: int = Field(default=0, ge=0, le=20)
    budget: float | None = Field(default=None, gt=0)
    styles: list[str] = Field(default_factory=list)
    special_needs: list[str] = Field(default_factory=list)
    transport_preferences: list[str] = Field(default_factory=list)
    accommodation_preferences: list[str] = Field(default_factory=list)
    food_preferences: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def destination_must_differ_from_origin(self):
        if self.origin == self.destination:
            raise ValueError("出发地和目的地不能相同")
        return self


class TravelRequirementDraft(BaseModel):
    """自然语言需求提取的中间结果；缺失字段不会被模型臆造。"""

    origin: str | None = None
    destination: str | None = None
    departure_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=30)
    adults: int = Field(default=1, ge=1, le=30)
    children: int = Field(default=0, ge=0, le=20)
    budget: float | None = Field(default=None, gt=0)
    styles: list[str] = Field(default_factory=list)
    special_needs: list[str] = Field(default_factory=list)

    def missing_fields(self) -> list[str]:
        labels = {"origin": "出发地", "destination": "目的地", "departure_date": "出发日期", "days": "出行天数"}
        return [label for field, label in labels.items() if getattr(self, field) is None]

    def to_requirement(self) -> TravelRequirement:
        missing = self.missing_fields()
        if missing:
            raise ValueError(f"旅行需求缺少：{', '.join(missing)}")
        return TravelRequirement(**self.model_dump())


class ResearchTask(BaseModel):
    """Planner 生成的可调度研究任务。"""

    id: str = Field(default_factory=lambda: uuid4().hex)
    task_type: TaskType
    query: str
    dependencies: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    """事实性结论的最小证据格式。"""

    content: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_url: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateOption(BaseModel):
    """Worker 返回的结构化候选项。"""

    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    category: str
    description: str = ""
    estimated_cost: float | None = Field(default=None, ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)


class WorkerResult(BaseModel):
    """所有专业 Worker 的统一输出。"""

    task_id: str
    worker: TaskType
    status: WorkerStatus
    summary: str
    options: list[CandidateOption] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TimeSlot(BaseModel):
    period: Literal["morning", "afternoon", "evening"]
    title: str
    description: str = ""
    estimated_cost: float | None = Field(default=None, ge=0)
    evidence_indexes: list[int] = Field(default_factory=list)


class ItineraryDay(BaseModel):
    day: int = Field(ge=1)
    date: date
    slots: list[TimeSlot]
    notes: list[str] = Field(default_factory=list)


class BudgetSummary(BaseModel):
    currency: str = "CNY"
    total_estimate: float | None = Field(default=None, ge=0)
    categories: dict[str, float | None] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class TravelPlanDraft(BaseModel):
    requirement: TravelRequirement
    itinerary: list[ItineraryDay]
    budget: BudgetSummary
    worker_results: list[WorkerResult]
    evidence: list[Evidence]
    warnings: list[str] = Field(default_factory=list)
    status: Literal["draft"] = "draft"
