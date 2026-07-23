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
