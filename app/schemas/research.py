"""Typed research and subagent result contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.planning import Evidence, TaskType, WorkerStatus


ResearchStatus = Literal["completed", "partial", "unavailable", "failed"]


class EvidenceBoundCandidate(BaseModel):
    """A candidate whose factual content is traceable to evidence IDs."""

    id: str | None = None
    name: str
    category: str = ""
    description: str = ""
    estimated_cost: float | None = Field(default=None, ge=0)
    attributes: dict[str, object] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class Claim(BaseModel):
    text: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class ResearchConflict(BaseModel):
    fact_key: str
    values: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    description: str = ""


class ResearchReport(BaseModel):
    status: ResearchStatus = "completed"
    summary: str = ""
    claims: list[Claim] = Field(default_factory=list)
    conflicts: list[ResearchConflict] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SubagentResponse(BaseModel):
    task_id: str
    worker: TaskType
    status: WorkerStatus
    summary: str = ""
    claims: list[Claim] = Field(default_factory=list)
    candidates: list[EvidenceBoundCandidate] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    research_report: ResearchReport | None = None
    warnings: list[str] = Field(default_factory=list)
