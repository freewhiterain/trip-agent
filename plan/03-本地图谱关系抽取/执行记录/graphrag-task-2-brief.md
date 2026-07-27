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

