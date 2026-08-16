from datetime import datetime, timedelta, timezone

import pytest

from app.agents.subagents.attractions import AttractionsSubagent
from app.agents.subagents.base import DomainSubagent
from app.agents.subagents.registry import SubagentRegistry
from app.agents.subagents.tools import ReadOnlyTool, build_subagent_tools
from app.agents.subagents.transport import TransportSubagent
from app.agents.supervisor import run_travel_planning
from app.governance.evidence import EvidenceGovernanceService
from app.governance.events import task_event_to_sse_event
from app.research.deep_search import DeepSearchEvaluation, DeepSearchRequest, run_deep_search
from app.schemas import planning as planning_schema
from app.schemas.governance import TaskEventRecord
from app.schemas.planning import Evidence, ResearchTask, TravelRequirement
from app.schemas.research import Claim, EvidenceBoundCandidate, ResearchReport, SubagentResponse


def _requirement() -> TravelRequirement:
    return TravelRequirement(
        origin="Shanghai",
        destination="Chengdu",
        departure_date="2026-08-01",
        days=3,
    )


def _task(worker: str) -> ResearchTask:
    return ResearchTask(id=f"{worker}-task", task_type=worker, query=f"Find {worker}")


@pytest.mark.asyncio
async def test_default_tool_builder_gates_external_tools_but_keeps_local_and_injected_tools(monkeypatch):
    from app.agents.subagents import tools as tools_module
    from app.mcp_core import client as client_module

    discovery_calls = []

    class FakeManager:
        async def get_allowed_tools(self, allowed):
            discovery_calls.append(allowed)
            return []

    class FakeKnowledge:
        def search_destination(self, destination, category, query):
            return [Evidence(content=query, source="local")]

    class FakeWeather:
        async def query(self, city, forecast=True):
            return [Evidence(content=city, source="test")]

    async def get_fake_manager():
        return FakeManager()

    monkeypatch.setattr(tools_module.settings, "enable_external_tools", False)
    monkeypatch.setattr(client_module, "get_mcp_client", get_fake_manager)

    weather_tools = await build_subagent_tools("weather")
    attraction_tools = await build_subagent_tools("attractions", knowledge=FakeKnowledge())
    injected_tools = await build_subagent_tools(
        "weather",
        mcp_manager=FakeManager(),
        weather_adapter=FakeWeather(),
    )

    assert len(discovery_calls) == 1
    assert weather_tools == []
    assert {tool.name for tool in attraction_tools} == {"local_rag"}
    assert {tool.name for tool in injected_tools} == {"weather_fallback_api"}


@pytest.mark.asyncio
async def test_deep_search_assigns_unique_stable_ids_to_idless_multi_round_evidence():
    calls = []

    async def search(query, limit):
        calls.append(query)
        return [Evidence(content=f"result for {query}", source="web", source_url=f"https://example.test/{len(calls)}")]

    class FollowUpOnce:
        calls = 0

        async def evaluate(self, state):
            self.calls += 1
            return DeepSearchEvaluation(
                needs_follow_up=self.calls == 1,
                follow_up_query="second round query",
            )

    report = await run_deep_search(
        DeepSearchRequest(query="first round query", worker="attractions", max_rounds=2),
        search=search,
        evaluator=FollowUpOnce(),
    )

    ids = [item.id for item in report.evidence]
    assert len(ids) == 2
    assert all(ids)
    assert len(set(ids)) == 2


class _CrossWorkerSubagent:
    async def run(self, task, requirement):
        if task.task_type == "attractions":
            evidence = Evidence(
                id="shared-search",
                content="Shared place is open.",
                source="search",
                source_url="https://example.test/shared",
            )
        elif task.task_type == "food":
            evidence = Evidence(
                id="shared-official",
                content="Shared place is open.",
                source="official",
                source_url="https://example.test/shared",
            )
        else:
            value = "open" if task.task_type == "weather" else "closed"
            evidence = Evidence(
                id=f"{task.task_type}-evidence",
                content=f"Cross-worker status is {value} for {task.task_type}.",
                source="official",
                source_url=f"https://example.test/{task.task_type}",
                metadata={"fact_key": "cross_worker_status", "fact_value": value},
            )
        return SubagentResponse(
            task_id=task.id,
            worker=task.task_type,
            status="completed",
            claims=[Claim(text=evidence.content, evidence_ids=[evidence.id])],
            candidates=[
                EvidenceBoundCandidate(
                    name=evidence.content,
                    category=task.task_type,
                    evidence_ids=[evidence.id],
                )
            ],
            evidence=[evidence],
        )


@pytest.mark.asyncio
async def test_supervisor_governs_cross_worker_evidence_after_fan_in():
    worker = _CrossWorkerSubagent()
    registry = SubagentRegistry(
        {name: worker for name in ("attractions", "weather", "transport", "hotel", "food")}
    )

    draft = await run_travel_planning(_requirement(), registry=registry)
    by_worker = {result.worker: result for result in draft.worker_results}

    shared = [item for item in draft.evidence if item.source_url == "https://example.test/shared"]
    assert [item.id for item in shared] == ["shared-official"]
    assert by_worker["attractions"].options[0].evidence_ids == ["shared-official"]
    assert by_worker["food"].options[0].evidence_ids == ["shared-official"]
    assert all(result.task_id for result in draft.worker_results)
    assert any(warning == "evidence_conflict:unresolved" for warning in draft.warnings)


@pytest.mark.asyncio
async def test_registry_and_supervisor_persist_only_stable_exception_codes():
    class ExplodingWorker:
        async def run(self, task, requirement):
            raise RuntimeError("secret provider payload")

    registry_result = await SubagentRegistry({"weather": ExplodingWorker()}).run(
        _task("weather"), _requirement()
    )
    assert registry_result.warnings == ["subagent_error:worker_execution_failed"]

    class ExplodingRegistry:
        async def run(self, task, requirement):
            raise RuntimeError("another secret provider payload")

    class RecordingEvents:
        def __init__(self):
            self.events = []

        async def emit(self, **event):
            self.events.append(event)

    events = RecordingEvents()
    await run_travel_planning(_requirement(), registry=ExplodingRegistry(), event_service=events)
    completed = [event for event in events.events if event["event_type"] == "worker_completed"]
    assert completed
    assert all(
        event["payload"]["warnings"] == ["subagent_error:supervisor_worker_failed"]
        for event in completed
    )
    assert "secret" not in repr(completed)


@pytest.mark.asyncio
async def test_typed_partial_evidence_continues_by_policy_without_forcing_deep_search():
    sufficiency = planning_schema.EvidenceSufficiency(
        status="partial", evidence_count=1, reason_code="provider_partial"
    )
    assert sufficiency.status == "partial"

    class Provider:
        def __init__(self, provider, result):
            self.provider = provider
            self.result = result
            self.calls = 0

        async def ainvoke(self, payload):
            self.calls += 1
            return self.result

    class FailDeepSearch:
        calls = 0

        async def __call__(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("transport must not require Deep Search")

    transport = Provider(
        "transport_mcp",
        {
            "status": "partial",
            "evidence": [Evidence(id="partial", content="Partial timetable.", source="test")],
        },
    )
    search = Provider("search_mcp", [])
    deep = FailDeepSearch()
    agent = TransportSubagent(transport_mcp=transport, search=search, deep_search=deep)

    result = await agent.run(_task("transport"), _requirement())

    assert transport.calls == 1
    assert search.calls == 1
    assert deep.calls == 0
    assert result.status == "partial"
    assert [item.id for item in result.evidence] == ["partial"]


@pytest.mark.asyncio
async def test_partial_evidence_can_continue_to_deep_search_when_policy_allows():
    class Provider:
        provider = "local_rag"

        async def ainvoke(self, payload):
            return {
                "status": "incomplete",
                "evidence": [Evidence(id="local-partial", content="Partial local fact.", source="local")],
            }

    class EmptySearch:
        provider = "search_mcp"

        async def ainvoke(self, payload):
            return []

    class DeepSearch:
        calls = 0

        async def __call__(self, request, **kwargs):
            self.calls += 1
            return ResearchReport(
                status="completed",
                evidence=[Evidence(id="deep-result", content="Deep Search fact.", source="local")],
            )

    deep = DeepSearch()
    agent = AttractionsSubagent(rag=Provider(), search=EmptySearch(), deep_search=deep)
    result = await agent.run(_task("attractions"), _requirement())

    assert deep.calls == 1
    assert {item.id for item in result.evidence} == {"local-partial", "deep-result"}


@pytest.mark.asyncio
async def test_runtime_worker_unavailability_marks_draft_degraded_even_with_llm_configured(monkeypatch):
    class UnavailableWorker:
        async def run(self, task, requirement):
            return SubagentResponse(
                task_id=task.id,
                worker=task.task_type,
                status="unavailable",
                warnings=["provider_unavailable"],
            )

    worker = UnavailableWorker()
    registry = SubagentRegistry(
        {name: worker for name in ("attractions", "weather", "transport", "hotel", "food")}
    )
    async def deterministic_synthesis(_requirement, _results, template):
        return template

    monkeypatch.setattr("app.agents.supervisor.settings.llm_api_key", "configured")
    monkeypatch.setattr(
        "app.agents.supervisor.synthesize_itinerary_with_llm",
        deterministic_synthesis,
    )

    draft = await run_travel_planning(_requirement(), registry=registry)

    assert draft.status == "degraded"
    assert draft.degraded_reason == "worker_unavailable"
    assert "planning_degraded:worker_unavailable" in draft.warnings


def test_governance_rejects_external_evidence_without_url_and_future_valid_from():
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    evidence = [
        Evidence(
            id="no-url",
            content="External without URL.",
            source="official",
            metadata={"source_type": "external"},
        ),
        Evidence(
            id="future",
            content="Future evidence.",
            source="official",
            source_url="https://example.test/future",
            valid_from=datetime(2026, 8, 1, 13),
        ),
        Evidence(
            id="current",
            content="Current evidence.",
            source="official",
            source_url="https://example.test/current",
            valid_from=datetime(2026, 8, 1, 11),
            valid_until=now + timedelta(hours=1),
        ),
        Evidence(id="local", content="Local evidence.", source="local"),
    ]
    response = SubagentResponse(
        task_id="task",
        worker="attractions",
        status="completed",
        claims=[Claim(text=item.content, evidence_ids=[item.id]) for item in evidence],
        evidence=evidence,
    )

    reviewed = EvidenceGovernanceService(now=now).review([response])

    assert {item.id for item in reviewed.evidence} == {"current", "local"}
    current = next(item for item in reviewed.evidence if item.id == "current")
    assert current.valid_from.tzinfo is not None
    assert {claim.evidence_ids[0] for claim in reviewed.claims} == {"current", "local"}


@pytest.mark.asyncio
async def test_tool_completion_event_is_emitted_after_call_and_sanitized_for_sse():
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
    await agent.run(_task("weather"), _requirement(), event_callback=on_event)

    completion = next(item for item in observed if item[0] == "subagent_tool_completed")
    assert completion[2] is True
    event = TaskEventRecord(
        task_id="planning-task",
        user_id="user",
        event_type="subagent_tool_completed",
        sequence=1,
        payload={
            "task_id": "weather-task",
            "worker": "weather",
            **completion[1],
            "warnings": ["secret upstream exception text"],
            "raw": {"secret": True},
        },
    )
    public = task_event_to_sse_event(event)

    assert public.type == "subagent_tool_completed"
    assert public.payload == {
        "task_id": "weather-task",
        "worker": "weather",
        "tool_name": "get_weather",
        "round_number": 1,
        "status": "sufficient",
        "evidence_count": 1,
    }
    assert "secret" not in public.model_dump_json()
