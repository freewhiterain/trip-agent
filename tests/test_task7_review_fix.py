from datetime import date
from types import SimpleNamespace

import pytest

from app.agents import factory
from app.agents.subagents.registry import SubagentRegistry
from app.agents.workers.registry import WorkerRegistry
from app.api.v1 import planning as planning_api
from app.api.v1 import tools as tools_api
from app.schemas.planning import BudgetSummary, TravelPlanDraft, TravelRequirement

from tests.test_trip_form_tool_flow import (
    FakeDraft,
    InMemoryInvocationRepository,
    configure_endpoint,
    endpoint_client,
    invocation,
    parse_sse,
)


def requirement() -> TravelRequirement:
    return TravelRequirement(
        destination="Chengdu",
        departure_date=date(2026, 9, 1),
        days=3,
    )


def draft_for(requirement: TravelRequirement) -> TravelPlanDraft:
    return TravelPlanDraft(
        requirement=requirement,
        itinerary=[],
        budget=BudgetSummary(),
        worker_results=[],
        evidence=[],
    )


def test_factory_selects_legacy_registry_for_supervisor_mode(monkeypatch):
    monkeypatch.setattr(factory.settings, "travel_agent_mode", "supervisor")

    registry, fallback_reason = factory.create_planning_registry()

    assert isinstance(registry, WorkerRegistry)
    assert fallback_reason is None


def test_factory_selects_subagent_registry_for_subagent_mode(monkeypatch):
    monkeypatch.setattr(factory.settings, "travel_agent_mode", "supervisor_subagents")
    monkeypatch.setattr(factory.settings, "dashscope_api_key", "configured")

    registry, fallback_reason = factory.create_planning_registry()

    assert isinstance(registry, SubagentRegistry)
    assert fallback_reason is None


@pytest.mark.asyncio
async def test_chat_agent_graph_receives_mode_selected_registry(monkeypatch):
    from app.agents import supervisor

    registry = object()
    captured = {}

    def fake_registry():
        return registry, None

    def fake_graph(*, registry):
        captured["registry"] = registry
        return "graph"

    monkeypatch.setattr(factory, "create_planning_registry", fake_registry)
    monkeypatch.setattr(supervisor, "create_supervisor_graph", fake_graph)

    assert await factory.create_chat_agent() == "graph"
    assert captured["registry"] is registry


@pytest.mark.asyncio
async def test_factory_fallback_marks_no_llm_planning_as_degraded(monkeypatch):
    monkeypatch.setattr(factory.settings, "travel_agent_mode", "supervisor_subagents")
    monkeypatch.setattr(factory.settings, "dashscope_api_key", "")
    monkeypatch.setattr(factory.settings, "allow_legacy_fallback", True)
    captured = {}

    async def fake_run(requirement, **kwargs):
        captured["registry"] = kwargs["registry"]
        return draft_for(requirement)

    monkeypatch.setattr("app.agents.supervisor.run_travel_planning", fake_run)

    result = await factory.run_travel_planning(requirement())

    assert isinstance(captured["registry"], WorkerRegistry)
    assert result.status == "degraded"
    assert "planning_degraded:no_llm_or_provider" in result.warnings


@pytest.mark.asyncio
async def test_planning_endpoint_uses_configured_factory_runner(monkeypatch):
    captured = {}

    async def fake_run(requirement, **kwargs):
        captured["requirement"] = requirement
        return draft_for(requirement)

    monkeypatch.setattr(factory, "run_travel_planning", fake_run)
    monkeypatch.setattr(planning_api, "PostgresPreferenceRepository", lambda: _EmptyPreferences())
    monkeypatch.setattr(planning_api, "PostgresEventRepository", lambda: None)
    monkeypatch.setattr(planning_api, "TaskEventService", lambda *_args: None)
    monkeypatch.setattr(planning_api, "get_checkpointer", lambda: _none())

    result = await planning_api.create_planning_task(requirement(), SimpleNamespace(id="user-1"))

    assert captured["requirement"].destination == "Chengdu"
    assert result["draft"]["status"] == "draft"


@pytest.mark.asyncio
async def test_tool_endpoint_uses_configured_factory_runner(monkeypatch):
    user = SimpleNamespace(id="user-1")
    repository = InMemoryInvocationRepository([invocation(user_id=user.id)])
    configure_endpoint(monkeypatch, repository)
    captured = {}

    async def fake_run(requirement, **kwargs):
        captured["requirement"] = requirement
        return FakeDraft(requirement.destination)

    monkeypatch.setattr(factory, "run_travel_planning", fake_run)

    async with endpoint_client(user) as client:
        response = await client.post(
            "/api/v1/chat/tools/call-1/result",
            json={
                "tool": "collect_trip_requirements",
                "status": "completed",
                "result": {
                    "destination": "Kyoto",
                    "departure_date": "2026-08-03",
                    "days": 4,
                },
            },
        )

    events = parse_sse(response)
    assert captured["requirement"].destination == "Kyoto"
    assert [event["type"] for event in events][-3:] == ["result", "token", "done"]


class _EmptyPreferences:
    async def list(self, _user_id):
        return []


async def _none():
    return None
