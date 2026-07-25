"""Attractions domain subagent."""

from __future__ import annotations

from app.agents.subagents.base import DomainSubagent
from app.schemas.planning import ResearchTask, TravelRequirement


class AttractionsSubagent(DomainSubagent):
    def __init__(self, **kwargs):
        super().__init__(
            worker="attractions",
            provider_order=("local_rag", "search_mcp"),
            **kwargs,
        )

    def build_query(self, task: ResearchTask, requirement: TravelRequirement) -> str:
        styles = " ".join(requirement.styles)
        return f"{task.query} {requirement.destination} {styles}".strip()


__all__ = ["AttractionsSubagent"]
