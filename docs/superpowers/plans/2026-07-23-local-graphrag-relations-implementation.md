# 本地知识图谱（轻量 GraphRAG）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不引入新基础设施的前提下，给本地知识库增加一层轻量实体关系图（存
Postgres），作为 `attractions`、`hotel` 两个 Worker 现有文档检索之外的第二证据
通道，用于回答"这个景点附近有什么住宿"这类关系型问题。

**Architecture:** 离线脚本从本地 Markdown 资料中做规则抽取（+ 可选 LLM 补充
抽取），写入 `knowledge_entity`/`knowledge_relation` 两张表；`GraphKnowledge
Service.search_related_entities` 按城市+类别查询 1-2 跳关系，转成与文档证据
同构的 `Evidence`；Worker 把图证据和文档证据合并后一起交给现有的
`analyze_worker_evidence`，不改变"无证据不产出候选"的约束。

**Tech Stack:** Python 3.11+, SQLAlchemy 2 (async, asyncpg), Pydantic 2,
LangChain `Document`, pytest, pytest-asyncio.

## Global Constraints

- 图数据存本项目已有的 PostgreSQL，不引入新的图数据库。
- 实体/关系抽取只在离线脚本 `scripts/build_knowledge_graph.py` 中运行，不在
  FastAPI 请求路径上，也不在 `Worker.run()` 里触发。
- LLM 未配置或单篇文档 LLM 抽取失败时，抽取脚本必须继续完成规则抽取部分，
  不中断、不产生半写入的脏数据。
- 图证据是文档证据的补充通道，不替代；`analyze_worker_evidence` 现有的
  "没有证据不产出候选"约束不变。
- `GraphKnowledgeService` 查询失败、图为空、表未建时返回空列表，不抛出异常到
  Worker。
- 本阶段只给 `attractions`、`hotel` 两个 Worker 接入图证据；`weather`、
  `transport`、`food` 不动。
- 不做社区检测、PageRank、超过 2 跳的遍历、实体消歧。
- 不引入 Alembic，沿用项目现有的 `Base.metadata.create_all` 方式建表。
- 直接在当前 `main` 工作区修改，不创建新分支，不自动提交 commit（除非用户
  明确要求）。
- 涉及真实 PostgreSQL 的测试遵循项目现有的 opt-in 约定：
  `pytest.mark.external` + `skipif(os.getenv("RUN_POSTGRES_TESTS") != "1", ...)`。

---

## File Structure

- Create: `app/models/knowledge_graph.py` — `KnowledgeEntity`、
  `KnowledgeRelation` SQLAlchemy 模型。
- Modify: `app/models/__init__.py` — 注册新模型。
- Create: `app/rag/graph_extraction.py` — 纯函数：规则抽取、可选 LLM 抽取、
  关系目标解析（`resolve_relations`）。不依赖数据库。
- Create: `app/agents/workers/graph_knowledge.py` — `GraphKnowledgeService`：
  写入抽取结果、按城市+类别查询关系证据。
- Create: `scripts/build_knowledge_graph.py` — 离线入口脚本。
- Modify: `app/agents/workers/attractions.py`、`app/agents/workers/hotel.py`
  — 合并图证据。
- Modify: `data/documents/attractions/chengdu.md`、
  `data/documents/accommodation/chengdu.md` — 补充具名实体和
  位于/临近关系描述。
- Create: `tests/test_graph_extraction.py` — 规则/LLM 抽取单元测试。
- Create: `tests/test_graph_knowledge_service.py` — 服务层单元测试（错误路径，
  不需要真实数据库）。
- Create: `tests/test_graph_knowledge_service_postgres.py` — opt-in 真实数据库
  测试（写入 + 查询）。
- Create: `tests/test_build_knowledge_graph.py` — 离线脚本编排测试。
- Create: `tests/test_graph_worker_integration.py` — Worker 合并图证据的集成
  测试。

---

## Task 1: Knowledge Graph Data Model

**Files:**
- Create: `app/models/knowledge_graph.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_graph_knowledge_service_postgres.py` (only the schema
  assertion in this task; more tests added in Task 4)

**Interfaces:**
- Produces: `KnowledgeEntity` (`id, city, category, name, source_document,
  attributes, created_at`), `KnowledgeRelation` (`id, from_entity_id,
  to_entity_id, relation_type, source_document, confidence, created_at`).

- [ ] **Step 1: Write the model file**

```python
# app/models/knowledge_graph.py
"""本地知识图谱：实体与关系。"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class KnowledgeEntity(Base):
    __table_args__ = (
        UniqueConstraint("city", "category", "name", name="uq_knowledge_entity_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    city: Mapped[str] = mapped_column(String(80), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(200))
    source_document: Mapped[str] = mapped_column(String(255))
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())


class KnowledgeRelation(Base):
    __table_args__ = (
        UniqueConstraint(
            "from_entity_id", "to_entity_id", "relation_type", name="uq_knowledge_relation_identity"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    from_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledgeentity.id", ondelete="CASCADE"), index=True
    )
    to_entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledgeentity.id", ondelete="CASCADE"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(40), index=True)
    source_document: Mapped[str] = mapped_column(String(255))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now())
```

Table names come from `Base.__tablename__` (`declared_attr`, lower-cased class
name) — `KnowledgeEntity` → `knowledgeentity`, matching the project's existing
convention (see `app/models/governance.py`, no model there sets an explicit
`__tablename__`). The `ForeignKey` targets must use the literal table name
`"knowledgeentity"`.

- [ ] **Step 2: Register the models**

```python
# app/models/__init__.py
from app.models.base import Base
from app.models.conversation import Conversation
from app.models.draft import TripDraft
from app.models.governance import Approval, SavedItinerary, TaskEvent, UserPreference
from app.models.knowledge_graph import KnowledgeEntity, KnowledgeRelation
from app.models.message import Message
from app.models.tool_invocation import ToolInvocation
from app.models.user import User

__all__ = [
    "Approval",
    "Base",
    "Conversation",
    "KnowledgeEntity",
    "KnowledgeRelation",
    "Message",
    "SavedItinerary",
    "TaskEvent",
    "ToolInvocation",
    "TripDraft",
    "User",
    "UserPreference",
]
```

- [ ] **Step 3: Write the failing opt-in schema test**

```python
# tests/test_graph_knowledge_service_postgres.py
import os
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

import app.models  # noqa: F401
from app.models.base import async_session_maker, init_db
from app.models.knowledge_graph import KnowledgeEntity, KnowledgeRelation


pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_TESTS") != "1",
        reason="requires RUN_POSTGRES_TESTS=1 and a reachable PostgreSQL database",
    ),
]


@pytest.mark.asyncio
async def test_knowledge_entity_identity_is_unique():
    await init_db()
    async with async_session_maker() as session, session.begin():
        session.add(
            KnowledgeEntity(
                city="__test_city__", category="attractions", name="__test_entity__",
                source_document="tests/fixture.md",
            )
        )

    with pytest.raises(IntegrityError):
        async with async_session_maker() as session, session.begin():
            session.add(
                KnowledgeEntity(
                    city="__test_city__", category="attractions", name="__test_entity__",
                    source_document="tests/fixture.md",
                )
            )

    # No KnowledgeRelation rows were created in this test, so only entities need cleanup.
    async with async_session_maker() as session, session.begin():
        await session.execute(
            KnowledgeEntity.__table__.delete().where(KnowledgeEntity.city == "__test_city__")
        )
```

- [ ] **Step 4: Run the test (skipped without a live database is expected)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_knowledge_service_postgres.py -q`

Expected: `1 skipped` when `RUN_POSTGRES_TESTS` is not set. If you have a
reachable local Postgres, run with
`RUN_POSTGRES_TESTS=1 .venv\Scripts\python.exe -m pytest tests/test_graph_knowledge_service_postgres.py -q`
and expect `1 passed`.

- [ ] **Step 5: Run compileall to catch import errors**

Run: `.venv\Scripts\python.exe -m compileall -q app/models`

Expected: exit code 0.

---

## Task 2: Rule-Based Entity And Relation Extraction

**Files:**
- Create: `app/rag/graph_extraction.py`
- Test: `tests/test_graph_extraction.py`

**Interfaces:**
- Consumes: `langchain_core.documents.Document` with `metadata["city"]`,
  `metadata["category"]`, `metadata["source"]` (already set by
  `DocumentManager` for mock markdown fixtures).
- Produces: `ExtractedEntity(city, category, name, source_document)`,
  `ExtractedRelation(city, from_name, from_category, relation_type, to_name,
  source_document, confidence)`, `ExtractionResult(entities, relations)`,
  `ResolvedRelation(from_city, from_category, from_name, to_city, to_category,
  to_name, relation_type, source_document, confidence)`.
- Produces: `extract_from_documents(documents: list[Document]) ->
  ExtractionResult`, `resolve_relations(city: str, known_entities:
  list[ExtractedEntity], relations: list[ExtractedRelation]) ->
  tuple[list[ExtractedEntity], list[ResolvedRelation]]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_graph_extraction.py
from langchain_core.documents import Document

from app.rag.graph_extraction import (
    ExtractedEntity,
    extract_entities,
    extract_from_documents,
    extract_relations,
    resolve_relations,
)


def attractions_doc() -> Document:
    return Document(
        page_content=(
            "# 成都景点模拟资料\n\n"
            "## 景点主题\n\n"
            "- 历史街区：适合检索传统建筑主题。\n\n"
            "### 宽窄巷子\n"
            "位于青羊区。是历史街区主题下的代表性步行游览区域。\n\n"
            "### 武侯祠\n"
            "位于武侯区。是博物馆与遗址主题下的代表性文化学习地点。\n"
        ),
        metadata={"city": "成都", "category": "attractions", "source": "data/documents/attractions/chengdu.md"},
    )


def accommodation_doc() -> Document:
    return Document(
        page_content=(
            "# 成都住宿模拟资料\n\n"
            "## 住宿选择线索\n\n"
            "### 青羊区住宿片区\n"
            "临近宽窄巷子。适合安排以历史街区步行游览为主的行程。\n"
        ),
        metadata={"city": "成都", "category": "hotel", "source": "data/documents/accommodation/chengdu.md"},
    )


def test_extract_entities_only_registers_level_three_headings():
    entities = extract_entities(attractions_doc())

    assert [entity.name for entity in entities] == ["宽窄巷子", "武侯祠"]
    assert all(entity.city == "成都" and entity.category == "attractions" for entity in entities)
    assert entities[0].source_document == "data/documents/attractions/chengdu.md"


def test_extract_entities_returns_empty_without_city_or_category_metadata():
    document = Document(page_content="### 无归属实体\n位于某地。", metadata={"source": "x.md"})

    assert extract_entities(document) == []


def test_extract_relations_finds_located_in_and_near():
    located_in = extract_relations(attractions_doc())
    near = extract_relations(accommodation_doc())

    assert [(r.from_name, r.relation_type, r.to_name) for r in located_in] == [
        ("宽窄巷子", "located_in", "青羊区"),
        ("武侯祠", "located_in", "武侯区"),
    ]
    assert [(r.from_name, r.relation_type, r.to_name) for r in near] == [
        ("青羊区住宿片区", "near", "宽窄巷子"),
    ]
    assert located_in[0].city == "成都"


def test_extract_from_documents_aggregates_entities_and_relations():
    result = extract_from_documents([attractions_doc(), accommodation_doc()])

    assert len(result.entities) == 3
    assert len(result.relations) == 3


def test_resolve_relations_auto_creates_area_entity_for_located_in():
    known = [ExtractedEntity(city="成都", category="attractions", name="宽窄巷子", source_document="a.md")]
    relations = extract_relations(attractions_doc())[:1]  # 宽窄巷子 located_in 青羊区

    extra_entities, resolved = resolve_relations("成都", known, relations)

    assert [entity.name for entity in extra_entities] == ["青羊区"]
    assert extra_entities[0].category == "area"
    assert len(resolved) == 1
    assert resolved[0].from_name == "宽窄巷子"
    assert resolved[0].to_name == "青羊区"
    assert resolved[0].to_category == "area"


def test_resolve_relations_skips_near_when_target_is_unknown():
    known = [ExtractedEntity(city="成都", category="hotel", name="青羊区住宿片区", source_document="h.md")]
    relations = extract_relations(accommodation_doc())  # near 宽窄巷子, not in known

    extra_entities, resolved = resolve_relations("成都", known, relations)

    assert extra_entities == []
    assert resolved == []


def test_resolve_relations_links_near_when_target_is_known():
    known = [
        ExtractedEntity(city="成都", category="attractions", name="宽窄巷子", source_document="a.md"),
        ExtractedEntity(city="成都", category="hotel", name="青羊区住宿片区", source_document="h.md"),
    ]
    relations = extract_relations(accommodation_doc())

    extra_entities, resolved = resolve_relations("成都", known, relations)

    assert extra_entities == []
    assert len(resolved) == 1
    assert resolved[0] == resolved[0]
    assert resolved[0].from_name == "青羊区住宿片区"
    assert resolved[0].to_name == "宽窄巷子"
    assert resolved[0].relation_type == "near"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_extraction.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.rag.graph_extraction'`.

- [ ] **Step 3: Implement the extraction module**

```python
# app/rag/graph_extraction.py
"""Rule-based (and optional LLM-assisted) entity/relation extraction for the
local knowledge graph. Pure functions only — no database access here."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from langchain_core.documents import Document


HEADING_PATTERN = re.compile(r"^### (.+)$", re.MULTILINE)
LOCATED_IN_PATTERN = re.compile(r"位于([^。，\n]{2,20})")
NEAR_PATTERN = re.compile(r"临近([^。，\n]{2,20})")


@dataclass
class ExtractedEntity:
    city: str
    category: str
    name: str
    source_document: str


@dataclass
class ExtractedRelation:
    city: str
    from_name: str
    from_category: str
    relation_type: str
    to_name: str
    source_document: str
    confidence: float = 1.0


@dataclass
class ResolvedRelation:
    from_city: str
    from_category: str
    from_name: str
    to_city: str
    to_category: str
    to_name: str
    relation_type: str
    source_document: str
    confidence: float = 1.0


@dataclass
class ExtractionResult:
    entities: list[ExtractedEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)


def extract_entities(document: Document) -> list[ExtractedEntity]:
    city = str(document.metadata.get("city", "")).strip()
    category = str(document.metadata.get("category", "")).strip()
    source = str(document.metadata.get("source", ""))
    if not city or not category:
        return []
    return [
        ExtractedEntity(city=city, category=category, name=heading.strip(), source_document=source)
        for heading in HEADING_PATTERN.findall(document.page_content)
        if heading.strip()
    ]


def _heading_sections(document: Document) -> list[tuple[str, str]]:
    """Split the document into (heading, body-until-next-heading) pairs for level-3 headings."""
    matches = list(HEADING_PATTERN.finditer(document.page_content))
    sections = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(document.page_content)
        sections.append((match.group(1).strip(), document.page_content[start:end]))
    return sections


def extract_relations(document: Document) -> list[ExtractedRelation]:
    city = str(document.metadata.get("city", "")).strip()
    category = str(document.metadata.get("category", "")).strip()
    source = str(document.metadata.get("source", ""))
    if not city or not category:
        return []
    relations: list[ExtractedRelation] = []
    for heading, body in _heading_sections(document):
        for target in LOCATED_IN_PATTERN.findall(body):
            relations.append(
                ExtractedRelation(
                    city=city, from_name=heading, from_category=category,
                    relation_type="located_in", to_name=target.strip(), source_document=source,
                )
            )
        for target in NEAR_PATTERN.findall(body):
            relations.append(
                ExtractedRelation(
                    city=city, from_name=heading, from_category=category,
                    relation_type="near", to_name=target.strip(), source_document=source,
                )
            )
    return relations


def extract_from_documents(documents: list[Document]) -> ExtractionResult:
    result = ExtractionResult()
    for document in documents:
        result.entities.extend(extract_entities(document))
        result.relations.extend(extract_relations(document))
    return result


def resolve_relations(
    city: str,
    known_entities: list[ExtractedEntity],
    relations: list[ExtractedRelation],
) -> tuple[list[ExtractedEntity], list[ResolvedRelation]]:
    """Resolve relation targets against already-known entities for one city.

    `located_in` auto-creates a `category="area"` entity when the target is
    unknown (districts rarely have their own heading). `near`/`connects_to`
    are skipped when the target entity is not already known, to avoid
    creating phantom entities from a dangling reference. Entity names are
    assumed unique within a city across categories (mock data is curated by
    hand; revisit if that stops holding).
    """
    known_by_name: dict[str, ExtractedEntity] = {entity.name: entity for entity in known_entities}
    extra_entities: list[ExtractedEntity] = []
    resolved: list[ResolvedRelation] = []

    for relation in relations:
        if relation.city != city:
            continue
        source = known_by_name.get(relation.from_name)
        if source is None:
            continue
        target = known_by_name.get(relation.to_name)
        if target is None:
            if relation.relation_type != "located_in":
                continue
            target = ExtractedEntity(
                city=city, category="area", name=relation.to_name, source_document=relation.source_document,
            )
            known_by_name[target.name] = target
            extra_entities.append(target)
        resolved.append(
            ResolvedRelation(
                from_city=source.city, from_category=source.category, from_name=source.name,
                to_city=target.city, to_category=target.category, to_name=target.name,
                relation_type=relation.relation_type, source_document=relation.source_document,
                confidence=relation.confidence,
            )
        )
    return extra_entities, resolved
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_extraction.py -q`

Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/rag/graph_extraction.py tests/test_graph_extraction.py
git commit -m "feat: add rule-based knowledge graph entity/relation extraction"
```

(Skip this step if the user has asked not to commit automatically — check
current session instructions before running.)

---

## Task 3: Optional LLM-Assisted Relation Extraction

**Files:**
- Modify: `app/rag/graph_extraction.py`
- Test: `tests/test_graph_extraction.py`

**Interfaces:**
- Consumes: an object exposing `.with_structured_output(schema)` returning an
  object with async `.ainvoke(messages) -> BaseModel` (same shape as the `llm`
  parameter already used by `app/agents/workers/rag_analysis.py`).
- Produces: `async extract_relations_with_llm(document: Document, llm) ->
  list[ExtractedRelation]`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_graph_extraction.py
import pytest

from app.rag.graph_extraction import extract_relations_with_llm


class _FakeStructuredLlm:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.messages = None

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, messages):
        self.messages = messages
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.asyncio
async def test_extract_relations_with_llm_returns_empty_when_llm_is_none():
    assert await extract_relations_with_llm(attractions_doc(), None) == []


@pytest.mark.asyncio
async def test_extract_relations_with_llm_returns_empty_on_failure():
    llm = _FakeStructuredLlm(error=RuntimeError("model unavailable"))

    assert await extract_relations_with_llm(attractions_doc(), llm) == []


@pytest.mark.asyncio
async def test_extract_relations_with_llm_maps_structured_response():
    from app.rag.graph_extraction import _LLMExtraction, _LLMRelation

    llm = _FakeStructuredLlm(
        response=_LLMExtraction(
            relations=[_LLMRelation(from_name="宽窄巷子", relation_type="near", to_name="武侯祠")]
        )
    )

    relations = await extract_relations_with_llm(attractions_doc(), llm)

    assert len(relations) == 1
    assert relations[0].from_name == "宽窄巷子"
    assert relations[0].relation_type == "near"
    assert relations[0].to_name == "武侯祠"
    assert relations[0].confidence == 0.6
    assert relations[0].city == "成都"
    prompt = "\n".join(message["content"] for message in llm.messages)
    assert "不得编造" in prompt
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_extraction.py -k llm -q`

Expected: FAIL with `ImportError: cannot import name 'extract_relations_with_llm'`.

- [ ] **Step 3: Implement the LLM-assisted extractor**

```python
# append to app/rag/graph_extraction.py
from typing import Any, Literal

from pydantic import BaseModel, Field


class _LLMRelation(BaseModel):
    from_name: str
    relation_type: Literal["located_in", "near", "connects_to"]
    to_name: str


class _LLMExtraction(BaseModel):
    relations: list[_LLMRelation] = Field(default_factory=list)


async def extract_relations_with_llm(document: Document, llm: Any | None) -> list[ExtractedRelation]:
    city = str(document.metadata.get("city", "")).strip()
    category = str(document.metadata.get("category", "")).strip()
    source = str(document.metadata.get("source", ""))
    if llm is None or not city or not category:
        return []
    try:
        structured = llm.with_structured_output(_LLMExtraction)
        response = await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "你是知识图谱抽取助手。只能使用给定文档内容识别实体之间的关系，"
                        "不得编造文档中不存在的实体或关系。relation_type 只能是 "
                        "located_in、near 或 connects_to。"
                    ),
                },
                {"role": "user", "content": document.page_content},
            ]
        )
        extraction = _LLMExtraction.model_validate(response)
    except Exception:
        return []
    return [
        ExtractedRelation(
            city=city, from_name=item.from_name.strip(), from_category=category,
            relation_type=item.relation_type, to_name=item.to_name.strip(),
            source_document=source, confidence=0.6,
        )
        for item in extraction.relations
        if item.from_name.strip() and item.to_name.strip()
    ]
```

- [ ] **Step 4: Run the full extraction test file**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_extraction.py -q`

Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add app/rag/graph_extraction.py tests/test_graph_extraction.py
git commit -m "feat: add optional LLM-assisted relation extraction with deterministic fallback"
```

(Skip if the user has asked not to auto-commit.)

---

## Task 4: GraphKnowledgeService (Persistence + Query)

**Files:**
- Create: `app/agents/workers/graph_knowledge.py`
- Test: `tests/test_graph_knowledge_service.py` (no database required)
- Test: `tests/test_graph_knowledge_service_postgres.py` (opt-in, extends
  Task 1's file)

**Interfaces:**
- Consumes: `ExtractedEntity`, `ResolvedRelation` from
  `app.rag.graph_extraction`; `Evidence`, `TaskType` from
  `app.schemas.planning`.
- Produces: `GraphKnowledgeService(session_factory=async_session_maker)` with
  `async write_entities_and_relations(entities: list[ExtractedEntity],
  relations: list[ResolvedRelation]) -> None` and `async
  search_related_entities(destination: str, category: TaskType, query: str)
  -> list[Evidence]`; `get_graph_knowledge_service() -> GraphKnowledgeService`
  (module-level singleton accessor, mirrors
  `app.agents.workers.local_knowledge.get_local_knowledge_service`).

- [ ] **Step 1: Write the failing error-path unit tests (no database)**

```python
# tests/test_graph_knowledge_service.py
import pytest

from app.agents.workers.graph_knowledge import GraphKnowledgeService


class _RaisingSessionFactory:
    def __call__(self):
        raise RuntimeError("database unavailable")


@pytest.mark.asyncio
async def test_search_related_entities_returns_empty_list_on_session_error():
    service = GraphKnowledgeService(session_factory=_RaisingSessionFactory())

    result = await service.search_related_entities("成都", "attractions", "宽窄巷子")

    assert result == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_knowledge_service.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named
'app.agents.workers.graph_knowledge'`.

- [ ] **Step 3: Implement the service**

```python
# app/agents/workers/graph_knowledge.py
"""Local knowledge graph persistence and query, used alongside document RAG."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models.base import async_session_maker
from app.models.knowledge_graph import KnowledgeEntity, KnowledgeRelation
from app.rag.graph_extraction import ExtractedEntity, ResolvedRelation
from app.schemas.planning import Evidence, TaskType
from app.utils.logger import app_logger


_RELATION_LABELS = {
    "located_in": "位于",
    "near": "临近",
    "connects_to": "连接到",
}


class GraphKnowledgeService:
    def __init__(self, session_factory=async_session_maker):
        self.session_factory = session_factory

    async def write_entities_and_relations(
        self,
        entities: list[ExtractedEntity],
        relations: list[ResolvedRelation],
    ) -> None:
        async with self.session_factory() as session, session.begin():
            entity_ids: dict[tuple[str, str, str], UUID] = {}
            for item in entities:
                statement = (
                    insert(KnowledgeEntity)
                    .values(
                        city=item.city, category=item.category, name=item.name,
                        source_document=item.source_document, attributes={},
                    )
                    .on_conflict_do_update(
                        constraint="uq_knowledge_entity_identity",
                        set_={"source_document": item.source_document},
                    )
                    .returning(KnowledgeEntity.id)
                )
                entity_id = (await session.execute(statement)).scalar_one()
                entity_ids[(item.city, item.category, item.name)] = entity_id

            for relation in relations:
                from_id = entity_ids.get((relation.from_city, relation.from_category, relation.from_name))
                to_id = entity_ids.get((relation.to_city, relation.to_category, relation.to_name))
                if from_id is None or to_id is None:
                    continue
                statement = (
                    insert(KnowledgeRelation)
                    .values(
                        from_entity_id=from_id, to_entity_id=to_id,
                        relation_type=relation.relation_type,
                        source_document=relation.source_document,
                        confidence=relation.confidence,
                    )
                    .on_conflict_do_nothing(constraint="uq_knowledge_relation_identity")
                )
                await session.execute(statement)

    async def search_related_entities(
        self,
        destination: str,
        category: TaskType,
        query: str,
    ) -> list[Evidence]:
        city = destination.strip()
        normalized_category = category.strip().casefold()
        try:
            async with self.session_factory() as session:
                entities = (
                    await session.execute(
                        select(KnowledgeEntity).where(
                            KnowledgeEntity.city == city,
                            KnowledgeEntity.category == normalized_category,
                        )
                    )
                ).scalars().all()
                if not entities:
                    return []
                entity_ids = [entity.id for entity in entities]
                relations = (
                    await session.execute(
                        select(KnowledgeRelation).where(KnowledgeRelation.from_entity_id.in_(entity_ids))
                    )
                ).scalars().all()
                if not relations:
                    return []
                target_ids = {relation.to_entity_id for relation in relations}
                targets = (
                    await session.execute(select(KnowledgeEntity).where(KnowledgeEntity.id.in_(target_ids)))
                ).scalars().all()
        except Exception as exc:
            app_logger.warning(f"知识图谱查询失败，返回空结果：{type(exc).__name__}: {exc}")
            return []

        entities_by_id = {entity.id: entity for entity in entities}
        targets_by_id = {entity.id: entity for entity in targets}
        evidence: list[Evidence] = []
        for relation in relations:
            source_entity = entities_by_id.get(relation.from_entity_id)
            target_entity = targets_by_id.get(relation.to_entity_id)
            if source_entity is None or target_entity is None:
                continue
            label = _RELATION_LABELS.get(relation.relation_type, relation.relation_type)
            evidence.append(
                Evidence(
                    content=f"{source_entity.name} {label} {target_entity.name}",
                    source=relation.source_document,
                    confidence=relation.confidence,
                    metadata={
                        "source_type": "graph_relation",
                        "category": normalized_category,
                        "relation_type": relation.relation_type,
                        "from_entity": source_entity.name,
                        "to_entity": target_entity.name,
                    },
                )
            )
        return evidence


_graph_knowledge_service: GraphKnowledgeService | None = None


def get_graph_knowledge_service() -> GraphKnowledgeService:
    global _graph_knowledge_service
    if _graph_knowledge_service is None:
        _graph_knowledge_service = GraphKnowledgeService()
    return _graph_knowledge_service
```

- [ ] **Step 4: Run the no-database test**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_knowledge_service.py -q`

Expected: PASS.

- [ ] **Step 5: Add the opt-in write+query round-trip test**

```python
# append to tests/test_graph_knowledge_service_postgres.py
from app.agents.workers.graph_knowledge import GraphKnowledgeService
from app.rag.graph_extraction import ExtractedEntity, ResolvedRelation


@pytest.mark.asyncio
async def test_write_and_search_round_trip_is_idempotent_and_category_scoped():
    await init_db()
    service = GraphKnowledgeService()
    entities = [
        ExtractedEntity(city="__test_city__", category="attractions", name="__attraction__", source_document="a.md"),
        ExtractedEntity(city="__test_city__", category="area", name="__area__", source_document="a.md"),
        ExtractedEntity(city="__test_city__", category="hotel", name="__hotel__", source_document="h.md"),
    ]
    relations = [
        ResolvedRelation(
            from_city="__test_city__", from_category="attractions", from_name="__attraction__",
            to_city="__test_city__", to_category="area", to_name="__area__",
            relation_type="located_in", source_document="a.md",
        ),
        ResolvedRelation(
            from_city="__test_city__", from_category="hotel", from_name="__hotel__",
            to_city="__test_city__", to_category="attractions", to_name="__attraction__",
            relation_type="near", source_document="h.md",
        ),
    ]

    try:
        await service.write_entities_and_relations(entities, relations)
        await service.write_entities_and_relations(entities, relations)  # must be idempotent

        attractions_evidence = await service.search_related_entities("__test_city__", "attractions", "q")
        hotel_evidence = await service.search_related_entities("__test_city__", "hotel", "q")
        weather_evidence = await service.search_related_entities("__test_city__", "weather", "q")

        assert [item.content for item in attractions_evidence] == ["__attraction__ 位于 __area__"]
        assert attractions_evidence[0].metadata["source_type"] == "graph_relation"
        assert [item.content for item in hotel_evidence] == ["__hotel__ 临近 __attraction__"]
        assert weather_evidence == []
    finally:
        async with async_session_maker() as session, session.begin():
            ids = (
                await session.execute(
                    select(KnowledgeEntity.id).where(KnowledgeEntity.city == "__test_city__")
                )
            ).scalars().all()
            if ids:
                await session.execute(
                    KnowledgeRelation.__table__.delete().where(
                        KnowledgeRelation.from_entity_id.in_(ids)
                    )
                )
                await session.execute(
                    KnowledgeEntity.__table__.delete().where(KnowledgeEntity.id.in_(ids))
                )
```

Add `from sqlalchemy import select` to that file's imports if not already
present from Task 1.

- [ ] **Step 6: Run the opt-in tests (skipped without RUN_POSTGRES_TESTS=1)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_knowledge_service_postgres.py -q`

Expected: `2 skipped` without a live database, `2 passed` with
`RUN_POSTGRES_TESTS=1` and a reachable Postgres.

- [ ] **Step 7: Commit**

```bash
git add app/agents/workers/graph_knowledge.py tests/test_graph_knowledge_service.py tests/test_graph_knowledge_service_postgres.py
git commit -m "feat: add GraphKnowledgeService for local knowledge graph persistence and query"
```

(Skip if the user has asked not to auto-commit.)

---

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

## Task 6: Wire Graph Evidence Into Attractions And Hotel Workers

**Files:**
- Modify: `app/agents/workers/attractions.py`
- Modify: `app/agents/workers/hotel.py`
- Modify: `data/documents/attractions/chengdu.md`
- Modify: `data/documents/accommodation/chengdu.md`
- Test: `tests/test_graph_worker_integration.py`

**Interfaces:**
- Consumes: `GraphKnowledgeService.search_related_entities` (Task 4).
- Produces: `AttractionsWorker(knowledge=None, llm=None, graph=None)`,
  `HotelWorker(knowledge=None, llm=None, graph=None)` — both merge document
  evidence and graph evidence before calling `analyze_worker_evidence`.

- [ ] **Step 1: Update the Chengdu mock fixtures with named entities**

```markdown
# data/documents/attractions/chengdu.md
# 成都景点模拟资料

数据类型：模拟资料
适用城市：成都
最后更新：开发测试数据

## 景点主题

- 熊猫文化：可作为自然教育与城市文化体验的检索线索。
- 历史街区：适合检索传统建筑、步行游览与本地生活方式主题。
- 博物馆与遗址：适合检索巴蜀历史、文物展示与文化学习主题。

### 成都大熊猫繁育研究基地
位于成华区。是熊猫文化主题下的代表性自然教育地点。

### 宽窄巷子
位于青羊区。是历史街区主题下的代表性步行游览区域。

### 武侯祠
位于武侯区。是博物馆与遗址主题下的代表性文化学习地点。

本资料用于开发测试，不提供实时开放状态、票价、预约名额或营业时间。
```

```markdown
# data/documents/accommodation/chengdu.md
# 成都住宿模拟资料

数据类型：模拟资料
适用城市：成都
最后更新：开发测试数据

## 住宿选择线索

- 选择住宿区域时，可优先比较与计划活动区域的通勤便利度。
- 家庭出行可关注房型空间、洗衣条件和安静程度等偏好。
- 短住行程可把抵达交通和返程衔接纳入位置选择。

### 青羊区住宿片区
临近宽窄巷子。适合安排以历史街区步行游览为主的行程。

### 武侯区住宿片区
临近武侯祠。适合安排以博物馆与遗址主题为主的行程。

本资料为开发测试资料，不提供实时房价、库存、可订状态或服务承诺。
```

- [ ] **Step 2: Run the Phase 2 document/RAG regression tests to confirm the fixture edit does not break existing assertions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py tests/test_phase2_rag_workers.py -q`

Expected: PASS. (These tests assert on the required boilerplate lines and
category metadata, which are unchanged; they do not assert on the exact
bullet content under `## 景点主题`/`## 住宿选择线索`.)

- [ ] **Step 3: Write the failing worker integration tests**

```python
# tests/test_graph_worker_integration.py
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
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_worker_integration.py -q`

Expected: FAIL with `TypeError: AttractionsWorker.__init__() got an unexpected keyword argument 'graph'`.

- [ ] **Step 5: Wire the graph service into both Workers**

```python
# app/agents/workers/attractions.py
from app.agents.workers.base import TravelWorker
from app.agents.workers.graph_knowledge import GraphKnowledgeService, get_graph_knowledge_service
from app.agents.workers.local_knowledge import LocalKnowledgeService, get_local_knowledge_service
from app.agents.workers.rag_analysis import analyze_worker_evidence, worker_result_from_analysis
from app.schemas.planning import ResearchTask, TravelRequirement, WorkerResult


class AttractionsWorker(TravelWorker):
    def __init__(
        self,
        knowledge: LocalKnowledgeService | None = None,
        llm=None,
        graph: GraphKnowledgeService | None = None,
    ):
        self.knowledge = knowledge
        self.llm = llm
        self.graph = graph

    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        document_evidence = (self.knowledge or get_local_knowledge_service()).search_destination(
            requirement.destination, "attractions", task.query
        )
        graph_evidence = await (self.graph or get_graph_knowledge_service()).search_related_entities(
            requirement.destination, "attractions", task.query
        )
        evidence = [*document_evidence, *graph_evidence]
        analysis = await analyze_worker_evidence(
            "attractions", task, requirement, evidence, llm=self.llm
        )
        return worker_result_from_analysis(task, "attractions", evidence, analysis)
```

```python
# app/agents/workers/hotel.py
from app.agents.workers.base import TravelWorker
from app.agents.workers.graph_knowledge import GraphKnowledgeService, get_graph_knowledge_service
from app.agents.workers.local_knowledge import LocalKnowledgeService, get_local_knowledge_service
from app.agents.workers.rag_analysis import analyze_worker_evidence, worker_result_from_analysis
from app.schemas.planning import ResearchTask, TravelRequirement, WorkerResult


class HotelWorker(TravelWorker):
    def __init__(
        self,
        knowledge: LocalKnowledgeService | None = None,
        llm=None,
        graph: GraphKnowledgeService | None = None,
    ):
        self.knowledge = knowledge
        self.llm = llm
        self.graph = graph

    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        query = f"{task.query} {' '.join(requirement.accommodation_preferences)}"
        document_evidence = (self.knowledge or get_local_knowledge_service()).search_destination(
            requirement.destination, "hotel", query
        )
        graph_evidence = await (self.graph or get_graph_knowledge_service()).search_related_entities(
            requirement.destination, "hotel", query
        )
        evidence = [*document_evidence, *graph_evidence]
        analysis = await analyze_worker_evidence("hotel", task, requirement, evidence, llm=self.llm)
        return worker_result_from_analysis(task, "hotel", evidence, analysis)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_worker_integration.py -q`

Expected: PASS (3 tests).

- [ ] **Step 7: Run the Phase 1/Phase 2 worker and Supervisor regression tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_rag_workers.py tests/test_phase1_supervisor.py tests/test_phase2_mock_rag_e2e.py -q`

Expected: PASS. `create_default_registry()` constructs `AttractionsWorker`/
`HotelWorker` with only `knowledge=` (no `graph=`), so they fall back to
`get_graph_knowledge_service()`, which in these tests either finds no
matching rows (empty city not seeded) or hits a database that has not been
migrated for this table in the test environment — both cases are caught by
`GraphKnowledgeService.search_related_entities`'s own `try/except` and
resolve to an empty list, so existing behavior is unchanged.

- [ ] **Step 8: Commit**

```bash
git add app/agents/workers/attractions.py app/agents/workers/hotel.py \
  data/documents/attractions/chengdu.md data/documents/accommodation/chengdu.md \
  tests/test_graph_worker_integration.py
git commit -m "feat: merge local knowledge graph evidence into attractions and hotel workers"
```

(Skip if the user has asked not to auto-commit.)

---

## Task 7: End-To-End Validation And Documentation

**Files:**
- Test: `tests/test_build_knowledge_graph.py` (one additional opt-in test)
- Modify: `README.md`
- Modify: `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: one opt-in end-to-end test proving
  "offline extraction -> Postgres write -> Worker query" against the real
  updated Chengdu fixtures; updated project documentation.

- [ ] **Step 1: Write the opt-in end-to-end test**

```python
# append to tests/test_build_knowledge_graph.py
import os

import pytest

from app.agents.workers.attractions import AttractionsWorker
from app.agents.workers.graph_knowledge import GraphKnowledgeService
from app.agents.workers.hotel import HotelWorker
from app.agents.workers.local_knowledge import LocalKnowledgeService
from app.models.base import async_session_maker, init_db
from app.models.knowledge_graph import KnowledgeEntity, KnowledgeRelation
from app.schemas.planning import ResearchTask, TravelRequirement
from datetime import date
from sqlalchemy import select

from scripts.build_knowledge_graph import build_graph


pytestmark_e2e = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_TESTS") != "1",
        reason="requires RUN_POSTGRES_TESTS=1 and a reachable PostgreSQL database",
    ),
]


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
```

- [ ] **Step 2: Run it (skipped without a live database is expected)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_build_knowledge_graph.py -q`

Expected: `3 passed, 1 skipped` without `RUN_POSTGRES_TESTS=1`; all 4 pass with
it set and a reachable Postgres.

- [ ] **Step 3: Update README**

Add to the "当前实现状态" section of `README.md`, after the existing Phase 2
mock-RAG bullet:

```markdown
- **本地知识图谱（轻量 GraphRAG）**：离线脚本 `scripts/build_knowledge_graph.py`
  从本地 Markdown 资料中抽取实体和"位于/临近"关系，写入 `knowledge_entity`/
  `knowledge_relation` 两张表；`attractions`、`hotel` 两个 Worker 会额外查询
  这层关系图，把结果作为 `source_type="graph_relation"` 的证据与文档证据合并
  使用。抽取只在离线脚本里发生，未配置 LLM 时仅产出规则抽取结果，不阻塞任何
  请求路径；图为空或查询异常时该 Worker 的行为与没有图谱时完全一致。
  `weather`/`transport`/`food` 三个 Worker 暂未接入图证据。
```

- [ ] **Step 4: Update progress.md**

```markdown
Plan: docs/superpowers/plans/2026-07-23-local-graphrag-relations-implementation.md
Local GraphRAG Task 1-7: complete (no commits by instruction unless requested; entities/relations tables + rule/LLM extraction + GraphKnowledgeService + offline build script + attractions/hotel worker integration; opt-in Postgres tests skipped without RUN_POSTGRES_TESTS=1, non-DB tests passed; Phase 1/Phase 2 regression suite unaffected)
```

- [ ] **Step 5: Run the full non-external test suite**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: all previously-passing tests still pass; the new opt-in tests report
as skipped (not failed) without `RUN_POSTGRES_TESTS=1`.

- [ ] **Step 6: Run compileall and the whitespace check**

Run: `.venv\Scripts\python.exe -m compileall -q app scripts tests`

Expected: exit code 0.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 7: Review the worktree without committing (unless the user has asked to commit)**

Run: `git status --short` and `git diff --stat`.

Expected: only the files listed in this plan's File Structure section are
new/modified, plus any pre-existing uncommitted changes from before this plan
started.
