"""Transport domain subagent."""

from __future__ import annotations

from app.agents.subagents.base import DomainSubagent
from app.schemas.planning import ResearchTask, TravelRequirement


class TransportSubagent(DomainSubagent):
    def __init__(self, **kwargs):
        super().__init__(
            worker="transport",
            provider_order=("transport_mcp", "search_mcp"),
            **kwargs,
        )

    def build_query(self, task: ResearchTask, requirement: TravelRequirement) -> str:
        preferences = " ".join(requirement.transport_preferences)
        origin = requirement.origin or "origin pending"
        return f"{origin} to {requirement.destination} {task.query} {preferences}".strip()


__all__ = ["TransportSubagent"]
