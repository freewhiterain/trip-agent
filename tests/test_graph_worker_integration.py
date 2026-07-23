from datetime import date

import pytest

from app.agents.workers.attractions import AttractionsWorker
from app.agents.workers.hotel import HotelWorker
from app.agents.workers.local_knowledge import LocalKnowledgeService
from app.schemas.planning import Evidence, ResearchTask, TravelRequirement
from langchain_core.documents import Document


def chengdu_requirement() -> TravelRequirement:
    return TravelRequirement(destination="成都", departure_date=date(2026, 9, 1), days=3)


class _FakeGraphService:
    def __init__(self, evidence: list[Evidence]):
        self._evidence = evidence
        self.calls: list[tuple[str, str, str]] = []

    async def search_related_entities(self, destination, category, query):
        self.calls.append((destination, category, query))
        return self._evidence


def local_knowledge_with_one_attraction() -> LocalKnowledgeService:
    return LocalKnowledgeService(
        documents=[
            Document(
                page_content="### 宽窄巷子\n位于青羊区，适合上午游览。",
                metadata={"source": "attractions/chengdu.md", "city": "成都", "category": "attractions", "source_type": "mock_markdown"},
            ),
        ]
    )


@pytest.mark.asyncio
async def test_attractions_worker_merges_graph_evidence_with_document_evidence():
    graph_evidence = [
        Evidence(
            content="宽窄巷子 位于 青羊区", source="attractions/chengdu.md",
            metadata={"source_type": "graph_relation", "category": "attractions"},
        )
    ]
    graph = _FakeGraphService(graph_evidence)
    worker = AttractionsWorker(knowledge=local_knowledge_with_one_attraction(), graph=graph)

    result = await worker.run(ResearchTask(task_type="attractions", query="成都 attractions"), chengdu_requirement())

    assert result.status in {"completed", "partial"}
    assert any(item.metadata.get("source_type") == "graph_relation" for item in result.evidence)
    assert any(item.metadata.get("source_type") == "mock_markdown" for item in result.evidence)
    assert graph.calls == [("成都", "attractions", "成都 attractions")]


@pytest.mark.asyncio
async def test_attractions_worker_unaffected_when_graph_service_returns_nothing():
    worker = AttractionsWorker(knowledge=local_knowledge_with_one_attraction(), graph=_FakeGraphService([]))

    result = await worker.run(ResearchTask(task_type="attractions", query="成都 attractions"), chengdu_requirement())

    assert result.status == "completed"
    assert all(item.metadata.get("source_type") != "graph_relation" for item in result.evidence)


@pytest.mark.asyncio
async def test_hotel_worker_returns_unavailable_when_both_document_and_graph_evidence_are_empty():
    worker = HotelWorker(knowledge=LocalKnowledgeService(documents=[]), graph=_FakeGraphService([]))

    result = await worker.run(ResearchTask(task_type="hotel", query="成都 hotel"), chengdu_requirement())

    assert result.status == "unavailable"
    assert result.evidence == []
