"""Safe normalization for read-only subagent tool results."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.planning import Evidence, TaskType


class ProviderError(BaseModel):
    """A safe, typed provider failure without raw upstream payloads."""

    model_config = ConfigDict(frozen=True)

    provider: str
    code: Literal["unavailable", "invalid_response", "empty_result"]
    message: str = Field(min_length=1)


def _error(provider: str, code: Literal["unavailable", "invalid_response", "empty_result"]) -> ProviderError:
    return ProviderError(provider=provider, code=code, message=f"{provider} provider is unavailable")


def normalize_tool_result(provider: str, payload: Any) -> list[Evidence | ProviderError]:
    """Keep only validated evidence or a sanitized provider error."""

    if isinstance(payload, BaseException):
        return [_error(provider, "unavailable")]

    raw_items: Any = payload
    if isinstance(payload, dict):
        status = payload.get("status")
        if status in {"failed", "unavailable"} or payload.get("error"):
            return [_error(provider, "unavailable")]
        raw_items = payload.get("evidence", [])

    if isinstance(raw_items, Evidence):
        return [raw_items]
    if not isinstance(raw_items, list):
        return [_error(provider, "invalid_response")]
    if not raw_items:
        return [_error(provider, "empty_result")]

    evidence: list[Evidence] = []
    for item in raw_items:
        try:
            evidence.append(item if isinstance(item, Evidence) else Evidence.model_validate(item))
        except Exception:
            return [_error(provider, "invalid_response")]
    return evidence


async def build_subagent_tools(
    worker: TaskType,
    policy: "ToolPolicy | None" = None,
    *,
    mcp_manager: Any | None = None,
) -> list[Any]:
    """Build only the MCP tools allowed by the worker's canonical policy."""

    from app.agents.subagents.tool_policy import ToolPolicy
    from app.mcp_core.client import get_mcp_client

    canonical = ToolPolicy.for_worker(worker)
    if policy is not None and policy.worker != worker:
        raise ValueError("Tool policy worker does not match requested worker")
    manager = mcp_manager or await get_mcp_client()
    return await manager.get_allowed_tools(canonical.allowed_tools)

