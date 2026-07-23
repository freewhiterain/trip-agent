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


@pytest.mark.asyncio
async def test_build_graph_writes_rule_extracted_relations_without_llm(monkeypatch):
    monkeypatch.setattr("scripts.build_knowledge_graph.settings.dashscope_api_key", "")
    documents = [
        mock_document("成都", "attractions", "### 宽窄巷子\n位于青羊区。\n", "attractions/chengdu.md"),
    ]
    service = _RecordingGraphService()

    await build_graph(document_manager=_StubDocumentManager(documents), service_factory=lambda: service)

    assert len(service.calls) == 1
    entities, relations = service.calls[0]
    assert {entity.name for entity in entities} == {"宽窄巷子", "青羊区"}
    assert len(relations) == 1
    assert relations[0].relation_type == "located_in"


@pytest.mark.asyncio
async def test_build_graph_skips_llm_extraction_when_not_configured(monkeypatch):
    monkeypatch.setattr("scripts.build_knowledge_graph.settings.dashscope_api_key", "")
    documents = [mock_document("成都", "attractions", "### 宽窄巷子\n无关系描述。\n", "a.md")]
    service = _RecordingGraphService()
    llm_factory_calls = []

    await build_graph(
        document_manager=_StubDocumentManager(documents),
        service_factory=lambda: service,
        llm_factory=lambda: llm_factory_calls.append(True),
    )

    assert llm_factory_calls == []  # llm_factory must not be invoked when no key is configured


@pytest.mark.asyncio
async def test_build_graph_continues_when_llm_extraction_fails(monkeypatch):
    monkeypatch.setattr("scripts.build_knowledge_graph.settings.dashscope_api_key", "fake-key")
    documents = [mock_document("成都", "attractions", "### 宽窄巷子\n位于青羊区。\n", "a.md")]
    service = _RecordingGraphService()

    class _ExplodingLlm:
        def with_structured_output(self, _schema):
            raise RuntimeError("no llm available in this test")

    await build_graph(
        document_manager=_StubDocumentManager(documents),
        service_factory=lambda: service,
        llm_factory=lambda: _ExplodingLlm(),
    )

    assert len(service.calls) == 1
    entities, relations = service.calls[0]
    assert len(relations) == 1  # rule-extracted relation still written despite LLM failure
