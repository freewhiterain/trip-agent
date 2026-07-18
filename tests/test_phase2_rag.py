from datetime import datetime, timedelta, timezone

from langchain_core.documents import Document

from app.rag.evidence import find_conflicts, is_evidence_fresh
from app.rag.reranker import RelevanceReranker
from app.rag.retriever import HybridRetriever
from app.rag.text_splitter import ParentDocumentSplitter
from app.schemas.planning import Evidence


def test_splitter_generates_stable_document_and_chunk_ids():
    source = Document(page_content="成都文化与美食。宽窄巷子。", metadata={"source": "chengdu.md"})
    splitter = ParentDocumentSplitter(parent_chunk_size=10, parent_chunk_overlap=2, child_chunk_size=5, child_chunk_overlap=1)

    first_parents, first_children = splitter.split_documents([source])
    second_parents, second_children = splitter.split_documents([source])

    assert [item.metadata["parent_id"] for item in first_parents] == [item.metadata["parent_id"] for item in second_parents]
    assert [item.metadata["chunk_id"] for item in first_children] == [item.metadata["chunk_id"] for item in second_children]


def test_rrf_merges_equivalent_documents_from_bm25_and_dense_sources():
    bm25_doc = Document(page_content="成都美食", metadata={"chunk_id": "same"})
    dense_clone = Document(page_content="成都美食", metadata={"chunk_id": "same"})
    other = Document(page_content="西安文化", metadata={"chunk_id": "other"})
    retriever = HybridRetriever(None, [bm25_doc, other], k=5)

    fused = retriever._rrf_fusion([(bm25_doc, 1.0), (other, 0.5)], [(dense_clone, 0.1)], k=60)

    assert [item.metadata["chunk_id"] for item in fused] == ["same", "other"]


def test_reranker_scores_and_reorders_instead_of_truncating():
    documents = [
        Document(page_content="酒店住宿"),
        Document(page_content="成都熊猫基地文化旅行"),
    ]
    reranker = RelevanceReranker()

    ranked = reranker.rerank("成都文化", documents, top_k=1)

    assert ranked[0].page_content == "成都熊猫基地文化旅行"
    assert ranked[0].metadata["rerank_score"] > 0


def test_bm25_only_retrieval_runs_without_vector_service():
    documents = [
        Document(page_content="成都熊猫基地", metadata={"chunk_id": "chengdu"}),
        Document(page_content="西安兵马俑", metadata={"chunk_id": "xian"}),
    ]
    retriever = HybridRetriever(None, documents, k=1, reranker=RelevanceReranker())

    result = retriever.retrieve("成都熊猫")

    assert result[0].metadata["chunk_id"] == "chengdu"


def test_evidence_freshness_and_conflict_detection():
    now = datetime.now(timezone.utc)
    stale = Evidence(
        content="旧天气",
        source="A",
        retrieved_at=now - timedelta(hours=2),
        valid_until=now - timedelta(hours=1),
        metadata={"fact_key": "weather", "fact_value": "rain"},
    )
    fresh = Evidence(
        content="新天气",
        source="B",
        retrieved_at=now,
        valid_until=now + timedelta(hours=1),
        metadata={"fact_key": "weather", "fact_value": "sunny"},
    )

    assert not is_evidence_fresh(stale, now)
    assert is_evidence_fresh(fresh, now)
    assert find_conflicts([stale, fresh]) == ["事实 weather 存在冲突值：rain, sunny"]
