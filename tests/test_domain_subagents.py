import pytest

from app.agents.subagents.attractions import AttractionsSubagent
from app.agents.subagents.food import FoodSubagent
from app.agents.subagents.hotel import HotelSubagent
from app.agents.subagents.registry import SubagentRegistry, create_default_subagent_registry
from app.agents.subagents.transport import TransportSubagent
from app.agents.subagents.weather import WeatherSubagent
from app.schemas.planning import Evidence, ResearchTask, TravelRequirement
from app.schemas.research import Claim, EvidenceBoundCandidate, ResearchReport
from app.agents.subagents.base import SubagentAnalysis


def requirement() -> TravelRequirement:
    return TravelRequirement(
        origin="Shanghai",
        destination="Chengdu",
        departure_date="2026-08-01",
        days=3,
        adults=2,
        styles=["family"],
        accommodation_preferences=["quiet"],
        food_preferences=["local"],
        transport_preferences=["train"],
    )


def task(worker: str, query: str | None = None) -> ResearchTask:
    return ResearchTask(
        id=f"{worker}-task",
        task_type=worker,
        query=query or f"Find {worker} options in Chengdu",
    )


class FakeProvider:
    def __init__(self, provider: str, result):
        self.provider = provider
        self.calls = []
        self._result = result

    async def ainvoke(self, payload):
        self.calls.append(payload)
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class FailIfCalled:
    calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        raise AssertionError("Deep Search must not be called for this worker")


class RecordingDeepSearch:
    def __init__(self, report: ResearchReport):
        self.calls = []
        self._report = report

    async def __call__(self, request, **kwargs):
        self.calls.append((request, kwargs))
        return self._report


@pytest.mark.asyncio
async def test_attractions_subagent_returns_evidence_bound_candidates():
    agent = AttractionsSubagent(
        rag=FakeProvider(
            "local_rag",
            [Evidence(id="ev-rag", content="Panda Base is a family-friendly attraction.", source="local")],
        ),
        search=FakeProvider("search_mcp", []),
    )

    result = await agent.run(task("attractions"), requirement())

    assert result.worker == "attractions"
    assert result.status == "completed"
    assert result.candidates
    assert all(item.evidence_ids for item in result.candidates)
    assert {eid for item in result.candidates for eid in item.evidence_ids} <= {"ev-rag"}


@pytest.mark.asyncio
async def test_weather_subagent_does_not_call_deep_search():
    deep_search = FailIfCalled()
    agent = WeatherSubagent(
        weather_mcp=FakeProvider(
            "weather_mcp",
            [Evidence(id="ev-weather", content="Warm with afternoon rain.", source="weather")],
        ),
        deep_search=deep_search,
    )

    result = await agent.run(task("weather"), requirement())

    assert result.status == "completed"
    assert result.worker == "weather"
    assert deep_search.calls == []


@pytest.mark.asyncio
async def test_transport_subagent_uses_search_fallback_without_deep_search():
    deep_search = FailIfCalled()
    transport = FakeProvider("transport_mcp", [])
    search = FakeProvider(
        "search_mcp",
        [Evidence(id="ev-search", content="High-speed train is available.", source="search")],
    )
    agent = TransportSubagent(
        transport_mcp=transport,
        search=search,
        deep_search=deep_search,
    )

    result = await agent.run(task("transport"), requirement())

    assert result.status == "completed"
    assert [payload["provider"] for payload in (transport.calls + search.calls)] == [
        "transport_mcp",
        "search_mcp",
    ]
    assert [item.id for item in result.evidence] == ["ev-search"]
    assert deep_search.calls == []


@pytest.mark.asyncio
async def test_hotel_subagent_falls_back_from_empty_rag_to_hotel_provider_before_search():
    rag = FakeProvider("local_rag", [])
    hotel = FakeProvider(
        "hotel_mcp",
        [Evidence(id="ev-hotel", content="Hotel near Tianfu Square has family rooms.", source="hotel")],
    )
    search = FakeProvider("search_mcp", [])
    agent = HotelSubagent(rag=rag, hotel_mcp=hotel, search=search)

    result = await agent.run(task("hotel"), requirement())

    assert result.status == "completed"
    assert [payload["provider"] for payload in (rag.calls + hotel.calls)] == [
        "local_rag",
        "hotel_mcp",
    ]
    assert search.calls == []
    assert result.candidates[0].evidence_ids == ["ev-hotel"]


@pytest.mark.asyncio
async def test_food_subagent_uses_bounded_deep_search_only_after_rag_and_search_are_empty():
    deep_report = ResearchReport(
        status="completed",
        summary="Found current food evidence.",
        evidence=[Evidence(id="ev-deep", content="Try mapo tofu at a current listed restaurant.", source="web")],
    )
    deep_search = RecordingDeepSearch(deep_report)
    agent = FoodSubagent(
        rag=FakeProvider("local_rag", []),
        search=FakeProvider("search_mcp", []),
        deep_search=deep_search,
    )

    result = await agent.run(task("food"), requirement())

    assert result.status == "completed"
    assert [call[0].worker for call in deep_search.calls] == ["food"]
    assert result.research_report == deep_report
    assert result.candidates[0].evidence_ids == ["ev-deep"]


def test_subagent_drops_content_not_supported_by_referenced_evidence():
    agent = AttractionsSubagent()
    analysis = SubagentAnalysis(
        summary="invented summary",
        claims=[Claim(text="invented claim", evidence_ids=["ev-1"])],
        candidates=[EvidenceBoundCandidate(
            name="invented place",
            description="invented details",
            estimated_cost=9999,
            attributes={"availability": "guaranteed"},
            evidence_ids=["ev-1"],
        )],
    )

    grounded, warnings = agent._ground_analysis(
        analysis,
        [Evidence(id="ev-1", content="Panda Base is open in the morning.", source="official")],
    )

    assert grounded.claims == []
    assert grounded.candidates == []
    assert grounded.summary == ""
    assert len(warnings) >= 2


@pytest.mark.asyncio
async def test_deep_search_failure_returns_structured_unavailable_response():
    async def failing_deep_search(*args, **kwargs):
        raise RuntimeError("provider secret should not escape")

    agent = FoodSubagent(
        rag=FakeProvider("local_rag", []),
        search=FakeProvider("search_mcp", []),
        deep_search=failing_deep_search,
    )

    result = await agent.run(task("food"), requirement())

    assert result.status == "unavailable"
    assert result.evidence == []
    assert any("deep search" in warning.lower() for warning in result.warnings)
    assert all("secret" not in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_subagent_registry_returns_structured_failure_for_unregistered_worker():
    registry = SubagentRegistry({})

    result = await registry.run(task("food"), requirement())

    assert result.status == "failed"
    assert result.worker == "food"
    assert result.candidates == []
    assert result.evidence == []


def test_default_subagent_registry_keeps_all_domain_workers_registered():
    registry = create_default_subagent_registry(build_tools=lambda worker: [])

    assert set(registry.workers) == {"attractions", "weather", "transport", "hotel", "food"}
