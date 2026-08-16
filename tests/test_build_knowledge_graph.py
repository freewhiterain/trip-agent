from langchain_core.documents import Document

import pytest

from scripts.build_knowledge_graph import build_graph


def mock_document(city, category, content, source):
    return Document(page_content=content, metadata={"city": city, "category": category, "source": source, "source_type": "mock_markdown"})


class _RecordingGraphService:
    def __init__(self):
        self.calls = []

    async def write_entities_and_relations(self, entities, relations):
        self.calls.append((list(entities), list(relations)))


class _StubDocumentManager:
    def __init__(self, documents):
        self._documents = documents

    def load_all_documents(self):
        return self._documents


async def _noop_ensure_schema():
    return None


@pytest.mark.asyncio
async def test_build_graph_writes_rule_extracted_relations_without_llm(monkeypatch):
    monkeypatch.setattr("scripts.build_knowledge_graph.settings.llm_api_key", "")
    documents = [
        mock_document("成都", "attractions", "### 宽窄巷子\n位于青羊区。\n", "attractions/chengdu.md"),
    ]
    service = _RecordingGraphService()

    await build_graph(
        document_manager=_StubDocumentManager(documents),
        service_factory=lambda: service,
        ensure_schema=_noop_ensure_schema,
    )

    assert len(service.calls) == 1
    entities, relations = service.calls[0]
    assert {entity.name for entity in entities} == {"宽窄巷子", "青羊区"}
    assert len(relations) == 1
    assert relations[0].relation_type == "located_in"


@pytest.mark.asyncio
async def test_build_graph_skips_llm_extraction_when_not_configured(monkeypatch):
    monkeypatch.setattr("scripts.build_knowledge_graph.settings.llm_api_key", "")
    documents = [mock_document("成都", "attractions", "### 宽窄巷子\n无关系描述。\n", "a.md")]
    service = _RecordingGraphService()
    llm_factory_calls = []

    await build_graph(
        document_manager=_StubDocumentManager(documents),
        service_factory=lambda: service,
        llm_factory=lambda: llm_factory_calls.append(True),
        ensure_schema=_noop_ensure_schema,
    )

    assert llm_factory_calls == []  # llm_factory must not be invoked when no key is configured


@pytest.mark.asyncio
async def test_build_graph_continues_when_llm_extraction_fails(monkeypatch):
    monkeypatch.setattr("scripts.build_knowledge_graph.settings.llm_api_key", "fake-key")
    documents = [mock_document("成都", "attractions", "### 宽窄巷子\n位于青羊区。\n", "a.md")]
    service = _RecordingGraphService()

    class _ExplodingLlm:
        def with_structured_output(self, _schema):
            raise RuntimeError("no llm available in this test")

    await build_graph(
        document_manager=_StubDocumentManager(documents),
        service_factory=lambda: service,
        llm_factory=lambda: _ExplodingLlm(),
        ensure_schema=_noop_ensure_schema,
    )

    assert len(service.calls) == 1
    entities, relations = service.calls[0]
    assert len(relations) == 1  # rule-extracted relation still written despite LLM failure


import os

from app.agents.workers.attractions import AttractionsWorker
from app.agents.workers.hotel import HotelWorker
from app.agents.workers.local_knowledge import LocalKnowledgeService
from app.models.base import init_db
from app.schemas.planning import ResearchTask, TravelRequirement
from datetime import date


@pytest.mark.external
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="requires RUN_POSTGRES_TESTS=1 and a reachable PostgreSQL database",
)
@pytest.mark.asyncio
async def test_real_chengdu_fixtures_produce_queryable_graph_evidence():
    await init_db()
    await build_graph()

    requirement = TravelRequirement(destination="成都", departure_date=date(2026, 9, 1), days=3)
    attractions_result = await AttractionsWorker(knowledge=LocalKnowledgeService()).run(
        ResearchTask(task_type="attractions", query="成都 attractions"), requirement
    )
    hotel_result = await HotelWorker(knowledge=LocalKnowledgeService()).run(
        ResearchTask(task_type="hotel", query="成都 hotel"), requirement
    )

    assert any(item.metadata.get("source_type") == "graph_relation" for item in attractions_result.evidence)
    assert any(item.metadata.get("source_type") == "graph_relation" for item in hotel_result.evidence)
    assert any("临近 宽窄巷子" in item.content for item in hotel_result.evidence)
