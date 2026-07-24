from langchain_core.documents import Document

from app.rag.text_splitter import ParentDocumentSplitter


def test_children_carry_section_title_from_nearest_heading():
    source = Document(
        page_content=(
            "### 成都大熊猫繁育研究基地\n位于成华区。是熊猫文化主题下的代表性自然教育地点。\n\n"
            "### 宽窄巷子\n位于青羊区。是历史街区主题下的代表性步行游览区域。\n"
        ),
        metadata={"source": "chengdu.md"},
    )
    splitter = ParentDocumentSplitter()

    _, children = splitter.split_documents([source])

    titles = {child.metadata["section_title"] for child in children}
    assert titles == {"成都大熊猫繁育研究基地", "宽窄巷子"}
    for child in children:
        if child.metadata["section_title"] == "成都大熊猫繁育研究基地":
            assert "成华区" in child.page_content


def test_lone_heading_merges_into_next_section_instead_of_becoming_empty_chunk():
    source = Document(
        page_content="### 孤立标题\n### 宽窄巷子\n位于青羊区。是历史街区主题下的代表性步行游览区域。\n",
        metadata={"source": "chengdu.md"},
    )
    splitter = ParentDocumentSplitter()

    _, children = splitter.split_documents([source])

    assert len(children) == 1
    assert children[0].metadata["section_title"] == "宽窄巷子"
    assert "孤立标题" in children[0].page_content
    assert "青羊区" in children[0].page_content


def test_no_heading_document_still_splits_by_character_size_as_before():
    source = Document(page_content="成都文化与美食。宽窄巷子。", metadata={"source": "chengdu.md"})
    splitter = ParentDocumentSplitter(
        parent_chunk_size=10, parent_chunk_overlap=2, child_chunk_size=5, child_chunk_overlap=1
    )

    parents, children = splitter.split_documents([source])

    assert len(parents) > 1
    assert all(child.metadata["section_title"] == "" for child in children)


def test_splitter_generates_stable_document_and_chunk_ids():
    source = Document(page_content="成都文化与美食。宽窄巷子。", metadata={"source": "chengdu.md"})
    splitter = ParentDocumentSplitter(
        parent_chunk_size=10, parent_chunk_overlap=2, child_chunk_size=5, child_chunk_overlap=1
    )

    first_parents, first_children = splitter.split_documents([source])
    second_parents, second_children = splitter.split_documents([source])

    assert [item.metadata["parent_id"] for item in first_parents] == [
        item.metadata["parent_id"] for item in second_parents
    ]
    assert [item.metadata["chunk_id"] for item in first_children] == [
        item.metadata["chunk_id"] for item in second_children
    ]
