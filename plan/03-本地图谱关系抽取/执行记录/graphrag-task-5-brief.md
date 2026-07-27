## Task 5: Offline Build Script

**Files:**
- Create: `scripts/build_knowledge_graph.py`
- Test: `tests/test_build_knowledge_graph.py`

**Interfaces:**
- Consumes: `DocumentManager().load_all_documents()`,
  `extract_from_documents`, `extract_relations_with_llm`, `resolve_relations`
  from `app.rag.graph_extraction`; `GraphKnowledgeService` from
  `app.agents.workers.graph_knowledge`; `settings.dashscope_api_key` from
  `app.config`.
- Produces: `async build_graph(*, document_manager=None,
  service_factory=GraphKnowledgeService, llm_factory=None) -> None` (keyword
  injection points exist purely for testability); `main()` sync entry point
  for `python scripts/build_knowledge_graph.py`.

- [ ] **Step 1: Write the failing orchestration tests**

```python
# tests/test_build_knowledge_graph.py
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_build_knowledge_graph.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.build_knowledge_graph'`.

- [ ] **Step 3: Add `scripts/__init__.py` if it does not already exist**

Check first: `Get-ChildItem scripts\__init__.py`. If it does not exist, create
an empty file at `scripts/__init__.py` so `scripts.build_knowledge_graph` is
importable from tests.

- [ ] **Step 4: Implement the script**

```python
# scripts/build_knowledge_graph.py
"""离线构建本地知识图谱：从模拟 Markdown 资料中抽取实体和关系，写入 Postgres。

不在 FastAPI 请求路径上运行，可重复执行（按唯一约束幂等）。
"""

from __future__ import annotations

import asyncio
from typing import Callable

from app.agents.workers.graph_knowledge import GraphKnowledgeService
from app.config import settings
from app.rag.document_loader import DocumentManager
from app.rag.graph_extraction import (
    ExtractedEntity,
    ExtractedRelation,
    extract_from_documents,
    extract_relations_with_llm,
    resolve_relations,
)
from app.utils.logger import app_logger


async def build_graph(
    *,
    document_manager: DocumentManager | None = None,
    service_factory: Callable[[], GraphKnowledgeService] = GraphKnowledgeService,
    llm_factory: Callable[[], object] | None = None,
) -> None:
    document_manager = document_manager or DocumentManager()
    documents = [
        document
        for document in document_manager.load_all_documents()
        if document.metadata.get("source_type") == "mock_markdown"
    ]
    result = extract_from_documents(documents)
    relations: list[ExtractedRelation] = list(result.relations)

    if settings.dashscope_api_key:
        if llm_factory is None:
            from app.agents.llm import get_llm as llm_factory  # type: ignore[assignment]
        try:
            llm = llm_factory()
        except Exception as exc:
            app_logger.warning(f"初始化 LLM 失败，跳过 LLM 补充抽取：{type(exc).__name__}: {exc}")
            llm = None
        if llm is not None:
            for document in documents:
                relations.extend(await extract_relations_with_llm(document, llm))
    else:
        app_logger.info("未配置 DASHSCOPE_API_KEY，跳过 LLM 补充抽取，仅写入规则抽取结果。")

    entities_by_city: dict[str, list[ExtractedEntity]] = {}
    for entity in result.entities:
        entities_by_city.setdefault(entity.city, []).append(entity)

    service = service_factory()
    for city, entities in entities_by_city.items():
        extra_entities, resolved = resolve_relations(city, entities, relations)
        await service.write_entities_and_relations(entities + extra_entities, resolved)
        app_logger.info(
            f"{city}: 写入 {len(entities) + len(extra_entities)} 个实体，{len(resolved)} 条关系。"
        )


def main() -> None:
    asyncio.run(build_graph())


if __name__ == "__main__":
    main()
```

Note: `extract_relations_with_llm` itself already catches its own exceptions
and returns `[]` on failure per document (Task 3); the `try/except` around
`llm_factory()` here additionally protects against the *construction* of the
LLM client failing (e.g. missing dependency), which the test in Step 1 does
not exercise directly but the guard is cheap and keeps the "never block on
LLM problems" constraint honest end-to-end.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_build_knowledge_graph.py -q`

Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add scripts/build_knowledge_graph.py scripts/__init__.py tests/test_build_knowledge_graph.py
git commit -m "feat: add offline knowledge graph build script"
```

(Skip if the user has asked not to auto-commit. Only add `scripts/__init__.py`
to the commit if it was newly created in Step 3.)

---

