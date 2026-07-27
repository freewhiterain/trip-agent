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

