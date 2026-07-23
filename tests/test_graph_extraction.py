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
