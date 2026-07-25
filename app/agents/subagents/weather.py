"""Weather domain subagent."""

from __future__ import annotations

from app.agents.subagents.base import DomainSubagent
from app.schemas.planning import ResearchTask, TravelRequirement


class WeatherSubagent(DomainSubagent):
    def __init__(self, **kwargs):
        super().__init__(
            worker="weather",
            provider_order=("weather_mcp", "weather_fallback_api"),
            **kwargs,
        )

    def build_query(self, task: ResearchTask, requirement: TravelRequirement) -> str:
        return (
            f"{task.query} {requirement.destination} "
            f"{requirement.departure_date.isoformat()} {requirement.days} days"
        )


__all__ = ["WeatherSubagent"]
