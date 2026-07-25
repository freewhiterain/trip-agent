"""Food domain subagent."""

from __future__ import annotations

from app.agents.subagents.base import DomainSubagent
from app.schemas.planning import ResearchTask, TravelRequirement


class FoodSubagent(DomainSubagent):
    def __init__(self, **kwargs):
        super().__init__(
            worker="food",
            provider_order=("local_rag", "search_mcp"),
            **kwargs,
        )

    def build_query(self, task: ResearchTask, requirement: TravelRequirement) -> str:
        preferences = " ".join(requirement.food_preferences)
        return f"{task.query} {requirement.destination} {preferences}".strip()


__all__ = ["FoodSubagent"]
