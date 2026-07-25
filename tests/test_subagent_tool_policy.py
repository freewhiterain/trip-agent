import pytest

from app.agents.subagents.tool_policy import ToolPolicy
from app.agents.subagents.tools import ProviderError, normalize_tool_result
from app.mcp_core.client import MCPClientManager
from app.schemas.planning import Evidence


def test_weather_policy_excludes_rag_and_deep_research():
    policy = ToolPolicy.for_worker("weather")

    assert policy.allowed_tools == {"weather_mcp", "weather_fallback_api"}
    assert policy.allow_deep_research is False


def test_attractions_policy_allows_rag_search_and_deep_research():
    policy = ToolPolicy.for_worker("attractions")

    assert {"local_rag", "search_mcp"}.issubset(policy.allowed_tools)
    assert policy.allow_deep_research is True


def test_policy_rejects_unknown_workers():
    with pytest.raises(ValueError, match="Unsupported subagent worker"):
        ToolPolicy.for_worker("payments")


@pytest.mark.asyncio
async def test_mcp_manager_exposes_only_allowlisted_read_only_tools():
    class FakeTool:
        def __init__(self, name: str):
            self.name = name

    class FakeClient:
        async def get_tools(self):
            return [
                FakeTool("get_weather"),
                FakeTool("web_search"),
                FakeTool("delete_all_trips"),
            ]

    manager = MCPClientManager()
    manager.client = FakeClient()

    tools = await manager.get_allowed_tools({"weather_mcp", "delete_all_trips"})

    assert [tool.name for tool in tools] == ["get_weather"]


def test_normalizer_returns_evidence_without_the_hidden_provider_payload():
    result = normalize_tool_result(
        "weather_mcp",
        {
            "status": "completed",
            "evidence": [{"content": "Clear skies", "source": "weather provider"}],
            "internal_trace": "do-not-return",
        },
    )

    assert len(result) == 1
    assert isinstance(result[0], Evidence)
    assert result[0].content == "Clear skies"
    assert result[0].source == "weather provider"
    assert not hasattr(result[0], "internal_trace")


def test_normalizer_converts_provider_exceptions_to_sanitized_typed_errors():
    result = normalize_tool_result(
        "weather_mcp",
        RuntimeError("request failed api_key=super-secret-token"),
    )

    assert len(result) == 1
    assert isinstance(result[0], ProviderError)
    assert result[0].provider == "weather_mcp"
    assert result[0].code == "unavailable"
    assert "super-secret-token" not in result[0].message
