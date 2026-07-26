from __future__ import annotations

import pytest

from app.agents.subagents.attractions import AttractionsSubagent
from app.agents.subagents.base import DomainSubagent
from app.agents.subagents.tools import ReadOnlyTool, build_subagent_tools
from app.governance.events import task_event_to_sse_event
from app.research.deep_search import DeepSearchEvaluation, DeepSearchRequest, run_deep_search
from app.schemas.governance import TaskEventRecord
from app.schemas.planning import Evidence, ResearchTask, TravelRequirement
from app.schemas.research import ResearchReport


def requirement() -> TravelRequirement:
    return TravelRequirement(
        origin="Shanghai",
        destination="Chengdu",
        departure_date="2026-08-01",
        days=3,
    )


def task(worker: str = "attractions") -> ResearchTask:
    return ResearchTask(id=f"{worker}-task", task_type=worker, query=f"Find {worker}")


@pytest.mark.asyncio
async def test_external_tools_are_disabled_before_discovery_and_adapter_construction(monkeypatch):
    from app.agents.subagents import tools as tools_module

    class FakeKnowledge:
        def search_destination(self, destination, category, query):
            return [Evidence(content=query, source="local")]

    async def fail_discovery():
        raise AssertionError("MCP discovery must be skipped")

    monkeypatch.setattr(tools_module.settings, "enable_external_tools", False)
    monkeypatch.setattr(tools_module, "get_mcp_client", fail_discovery)

    tools = await build_subagent_tools("attractions", knowledge=FakeKnowledge())

    assert [tool.name for tool in tools] == ["local_rag"]


@pytest.mark.asyncio
async def test_explicit_external_injections_remain_available_when_external_tools_are_disabled(monkeypatch):
    from app.agents.subagents import tools as tools_module

    class FakeManager:
        async def get_allowed_tools(self, allowed):
            class FakeTool:
                name = "get_weather"

            return [FakeTool()]

    class FakeWeather:
        async def query(self, city, forecast=True):
            return [Evidence(content=city, source="test")]

    monkeypatch.setattr(tools_module.settings, "enable_external_tools", False)

    tools = await build_subagent_tools(
        "weather",
        mcp_manager=FakeManager(),
        weather_adapter=FakeWeather(),
    )

    assert {tool.name for tool in tools} == {"get_weather", "weather_fallback_api"}


@pytest.mark.asyncio
async def test_deep_search_assigns_stable_unique_ids_to_idless_evidence_across_rounds():
    calls = 0

    async def search(query, limit):
        nonlocal calls
        calls += 1
        return [
            Evidence(
                content=f"provider content round {calls}",
                source="web",
                source_url=f"https://example.test/{calls}",
            )
        ]

    class FollowUpOnce:
        calls = 0

        async def evaluate(self, state):
            self.calls += 1
            return DeepSearchEvaluation(
                needs_follow_up=self.calls == 1,
                follow_up_query="second round",
            )

    request = DeepSearchRequest(query="first round", worker="attractions", max_rounds=2)
    first = await run_deep_search(request, search=search, evaluator=FollowUpOnce())

    first_ids = [item.id for item in first.evidence]
    assert all(first_ids)
    assert len(set(first_ids)) == 2

    calls = 0
    second = await run_deep_search(
        request,
        search=search,
        evaluator=FollowUpOnce(),
    )
    assert [item.id for item in second.evidence] == first_ids


@pytest.mark.asyncio
async def test_partial_provider_result_continues_to_next_provider():
    class Provider:
        def __init__(self, result):
            self.result = result
            self.calls = 0

        async def ainvoke(self, payload):
            self.calls += 1
            return self.result

    rag = Provider(
        {
            "status": "partial",
            "evidence": [Evidence(id="partial-rag", content="Local partial fact.", source="local")],
        }
    )
    search = Provider(
        {
            "status": "completed",
            "evidence": [Evidence(id="search-result", content="Search fact.", source="search")],
        }
    )

    agent = AttractionsSubagent(rag=rag, search=search)
    result = await agent.run(task(), requirement())

    assert rag.calls == 1
    assert search.calls == 1
    assert {item.id for item in result.evidence} == {"partial-rag", "search-result"}


@pytest.mark.asyncio
async def test_partial_provider_result_can_continue_to_deep_search():
    class PartialProvider:
        async def ainvoke(self, payload):
            return {
                "status": "incomplete",
                "evidence": [Evidence(id="partial-rag", content="Local partial fact.", source="local")],
            }

    class EmptyProvider:
        async def ainvoke(self, payload):
            return []

    class DeepSearch:
        calls = 0

        async def __call__(self, request, **kwargs):
            self.calls += 1
            return ResearchReport(
                status="completed",
                evidence=[Evidence(id="deep-result", content="Deep Search fact.", source="web")],
            )

    deep = DeepSearch()
    agent = AttractionsSubagent(
        rag=PartialProvider(),
        search=EmptyProvider(),
        deep_search=deep,
    )

    result = await agent.run(task(), requirement())

    assert deep.calls == 1
    assert {item.id for item in result.evidence} == {"partial-rag", "deep-result"}


@pytest.mark.asyncio
async def test_tool_completion_event_is_emitted_after_provider_call_with_safe_metadata():
    provider_called = False
    observed = []

    async def provider(payload):
        nonlocal provider_called
        provider_called = True
        return [Evidence(id="weather", content="Clear.", source="test")]

    async def on_event(event_type, payload):
        observed.append((event_type, payload, provider_called))

    agent = DomainSubagent(
        worker="weather",
        provider_order=("weather_mcp",),
        tools=[ReadOnlyTool(name="get_weather", provider="weather_mcp", _handler=provider)],
    )
    await agent.run(task("weather"), requirement(), event_callback=on_event)

    completion = next(item for item in observed if item[0] == "subagent_tool_completed")
    assert completion[2] is True
    assert completion[1] == {
        "tool_name": "get_weather",
        "round_number": 1,
        "status": "sufficient",
        "evidence_count": 1,
    }


@pytest.mark.asyncio
async def test_failed_tool_completion_event_contains_no_raw_exception_text():
    observed = []

    async def provider(payload):
        raise RuntimeError("secret upstream payload")

    async def on_event(event_type, payload):
        observed.append((event_type, payload))

    agent = DomainSubagent(
        worker="weather",
        provider_order=("weather_mcp",),
        tools=[ReadOnlyTool(name="get_weather", provider="weather_mcp", _handler=provider)],
    )
    await agent.run(task("weather"), requirement(), event_callback=on_event)

    completion = next(item for item in observed if item[0] == "subagent_tool_completed")
    assert completion[1]["status"] == "failed"
    assert "secret" not in repr(observed)

    public = task_event_to_sse_event(
        TaskEventRecord(
            task_id="planning-task",
            user_id="user",
            event_type="subagent_tool_completed",
            sequence=1,
            payload={
                "task_id": "weather-task",
                "worker": "weather",
                **completion[1],
                "warnings": ["secret upstream exception text"],
            },
        )
    )

    assert public is not None
    assert public.type == "subagent_tool_completed"
    assert public.payload == {
        "task_id": "weather-task",
        "worker": "weather",
        "tool_name": "get_weather",
        "round_number": 1,
        "status": "failed",
        "evidence_count": 0,
        "warning_codes": ["provider_unavailable"],
    }
    assert "secret" not in public.model_dump_json()
