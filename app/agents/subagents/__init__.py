"""Domain subagent building blocks with lazy exports to avoid import cycles."""

from importlib import import_module


_EXPORTS = {
    "AttractionsSubagent": ("app.agents.subagents.attractions", "AttractionsSubagent"),
    "DomainSubagent": ("app.agents.subagents.base", "DomainSubagent"),
    "FoodSubagent": ("app.agents.subagents.food", "FoodSubagent"),
    "HotelSubagent": ("app.agents.subagents.hotel", "HotelSubagent"),
    "SubagentRegistry": ("app.agents.subagents.registry", "SubagentRegistry"),
    "TransportSubagent": ("app.agents.subagents.transport", "TransportSubagent"),
    "WeatherSubagent": ("app.agents.subagents.weather", "WeatherSubagent"),
    "create_default_subagent_registry": (
        "app.agents.subagents.registry",
        "create_default_subagent_registry",
    ),
}


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    return getattr(import_module(module_name), attribute)


__all__ = list(_EXPORTS)
