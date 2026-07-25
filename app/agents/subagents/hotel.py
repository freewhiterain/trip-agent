"""Hotel domain subagent."""

from __future__ import annotations

from app.agents.subagents.base import DomainSubagent
from app.schemas.planning import ResearchTask, TravelRequirement


class HotelSubagent(DomainSubagent):
    def __init__(self, **kwargs):
        super().__init__(
            worker="hotel",
            provider_order=("local_rag", "hotel_mcp", "search_mcp"),
            **kwargs,
        )

    def build_query(self, task: ResearchTask, requirement: TravelRequirement) -> str:
        preferences = " ".join(requirement.accommodation_preferences)
        return f"{task.query} {requirement.destination} {preferences}".strip()


__all__ = ["HotelSubagent"]
