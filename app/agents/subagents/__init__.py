"""Domain subagent building blocks."""

from app.agents.subagents.attractions import AttractionsSubagent
from app.agents.subagents.base import DomainSubagent
from app.agents.subagents.food import FoodSubagent
from app.agents.subagents.hotel import HotelSubagent
from app.agents.subagents.registry import SubagentRegistry, create_default_subagent_registry
from app.agents.subagents.transport import TransportSubagent
from app.agents.subagents.weather import WeatherSubagent

__all__ = [
    "AttractionsSubagent",
    "DomainSubagent",
    "FoodSubagent",
    "HotelSubagent",
    "SubagentRegistry",
    "TransportSubagent",
    "WeatherSubagent",
    "create_default_subagent_registry",
]
