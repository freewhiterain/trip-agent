"""Safe normalization for read-only subagent tool results."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.planning import Evidence, TaskType


class ProviderError(BaseModel):
    """A safe, typed provider failure without raw upstream payloads."""

    model_config = ConfigDict(frozen=True)

    provider: str
    code: Literal["unavailable", "invalid_response", "empty_result"]
    message: str = Field(min_length=1)


@dataclass(slots=True)
class ReadOnlyTool:
    """Adapter that prevents raw provider results from reaching a subagent."""

    name: str
    provider: str
    _handler: Callable[[Any], Any]

    async def ainvoke(self, payload: Any) -> list[Evidence | ProviderError]:
        try:
            result = self._handler(payload)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            return normalize_tool_result(self.provider, exc)
        return normalize_tool_result(self.provider, result)


def _error(provider: str, code: Literal["unavailable", "invalid_response", "empty_result"]) -> ProviderError:
    return ProviderError(provider=provider, code=code, message=f"{provider} provider is unavailable")


_HIDDEN_METADATA_KEYS = {
    "api_key", "authorization", "headers", "internal_trace", "password",
    "raw", "request", "response", "secret", "token",
}


def _sanitize_evidence(item: Evidence | Mapping[str, Any]) -> Evidence:
    if isinstance(item, Evidence):
        data = item.model_dump(mode="python")
    else:
        data = dict(item)
    metadata = data.get("metadata") or {}
    if isinstance(metadata, Mapping):
        data["metadata"] = {
            key: value
            for key, value in metadata.items()
            if str(key).casefold() not in _HIDDEN_METADATA_KEYS
        }
    else:
        data["metadata"] = {}
    return Evidence.model_validate(data)


def normalize_tool_result(provider: str, payload: Any) -> list[Evidence | ProviderError]:
    """Keep only validated evidence or a sanitized provider error."""

    if isinstance(payload, BaseException):
        return [_error(provider, "unavailable")]

    raw_items: Any = payload
    if isinstance(payload, BaseModel):
        raw_items = payload.model_dump(mode="python")
    if isinstance(raw_items, Mapping):
        status = raw_items.get("status")
        if status in {"failed", "unavailable"} or raw_items.get("error"):
            return [_error(provider, "unavailable")]
        raw_items = raw_items.get("evidence", [])

    if isinstance(raw_items, Evidence):
        return [_sanitize_evidence(raw_items)]
    if not isinstance(raw_items, list):
        return [_error(provider, "invalid_response")]
    if not raw_items:
        return [_error(provider, "empty_result")]

    evidence: list[Evidence] = []
    for item in raw_items:
        try:
            evidence.append(_sanitize_evidence(item))
        except Exception:
            return [_error(provider, "invalid_response")]
    return evidence


async def build_subagent_tools(
    worker: TaskType,
    policy: "ToolPolicy | None" = None,
    *,
    mcp_manager: Any | None = None,
    knowledge: Any | None = None,
    weather_adapter: Any | None = None,
    search_adapter: Any | None = None,
    deep_research_service: Any | None = None,
) -> list[Any]:
    """Build normalized MCP and non-MCP tools for one domain worker."""

    from app.agents.subagents.tool_policy import ToolPolicy
    from app.mcp_core.client import get_mcp_client

    canonical = ToolPolicy.for_worker(worker)
    if policy is not None and policy.worker != worker:
        raise ValueError("Tool policy worker does not match requested worker")
    manager = mcp_manager or await get_mcp_client()
    mcp_tools = await manager.get_allowed_tools(canonical.allowed_tools)
    tools: list[ReadOnlyTool] = []
    mcp_providers = {
        "get_weather": "weather_mcp",
        "get_transport": "transport_mcp",
        "search_hotels": "hotel_mcp",
        "get_hotel_availability": "hotel_mcp",
        "web_search": "search_mcp",
    }
    for tool in mcp_tools:
        name = getattr(tool, "name", "")
        provider = mcp_providers.get(name)
        if provider is None:
            continue

        async def invoke_mcp(payload: Any, *, source=tool) -> Any:
            method = getattr(source, "ainvoke", None) or getattr(source, "arun", None)
            if method is not None:
                return await method(payload)
            return source.invoke(payload)

        tools.append(ReadOnlyTool(name=name, provider=provider, _handler=invoke_mcp))

    if "local_rag" in canonical.allowed_tools:
        if knowledge is None:
            from app.agents.workers.local_knowledge import get_local_knowledge_service
            knowledge = get_local_knowledge_service()

        async def invoke_rag(payload: Mapping[str, Any]) -> Any:
            return knowledge.search_destination(
                str(payload["destination"]),
                payload.get("category", worker),
                str(payload["query"]),
            )

        tools.append(ReadOnlyTool(name="local_rag", provider="local_rag", _handler=invoke_rag))

    if "weather_fallback_api" in canonical.allowed_tools:
        if weather_adapter is None:
            from app.mcp_core.adapters.weather import AmapWeatherAdapter
            weather_adapter = AmapWeatherAdapter()

        async def invoke_weather(payload: Mapping[str, Any]) -> Any:
            return await weather_adapter.query(
                str(payload["city"]), bool(payload.get("forecast", True))
            )

        tools.append(ReadOnlyTool(
            name="weather_fallback_api",
            provider="weather_fallback_api",
            _handler=invoke_weather,
        ))

    if "deep_research" in canonical.allowed_tools:
        if deep_research_service is None:
            from app.research.deep_research import DeepResearchService
            from app.mcp_core.adapters.search import TavilySearchAdapter
            search_adapter = search_adapter or TavilySearchAdapter()
            deep_research_service = DeepResearchService(search_adapter.search)

        async def invoke_deep_research(payload: Mapping[str, Any]) -> Any:
            return await deep_research_service.research(
                str(payload["query"]),
                int(payload.get("max_results_per_query", 3)),
            )

        tools.append(ReadOnlyTool(
            name="deep_research",
            provider="deep_research",
            _handler=invoke_deep_research,
        ))

    return tools
