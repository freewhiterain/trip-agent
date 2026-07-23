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
