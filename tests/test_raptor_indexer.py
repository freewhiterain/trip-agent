from langchain_core.documents import Document

from app.rag.raptor import RaptorIndexer


def test_build_tree_creates_one_summary_per_document_group():
    chunks = [
        Document(page_content="宽窄巷子是历史街区。", metadata={"document_id": "doc-a", "chunk_id": "a1"}),
        Document(page_content="宽窄巷子适合步行游览。", metadata={"document_id": "doc-a", "chunk_id": "a2"}),
        Document(page_content="武侯祠是博物馆与遗址。", metadata={"document_id": "doc-b", "chunk_id": "b1"}),
    ]
    indexer = RaptorIndexer()

    summaries = indexer.build_tree(chunks)

    assert len(summaries) == 2
    assert all(summary.metadata["is_raptor_summary"] for summary in summaries)
    document_ids = {summary.metadata["document_id"] for summary in summaries}
    assert document_ids == {"doc-a", "doc-b"}


def test_build_tree_does_not_recurse_or_mutate_original_chunks():
    chunk = Document(page_content="宽窄巷子是历史街区。", metadata={"document_id": "doc-a", "chunk_id": "a1"})
    indexer = RaptorIndexer()

    summaries = indexer.build_tree([chunk])

    assert chunk.metadata.get("is_raptor_summary") is None
    assert len(summaries) == 1
    assert summaries[0].page_content == "宽窄巷子是历史街区。"[:80]
