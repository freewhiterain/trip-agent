from langchain_core.documents import Document

from app.rag.retriever import HybridRetriever


class _FakeVectorstore:
    def __init__(self, results=None, error: Exception | None = None):
        self._results = results or []
        self._error = error
        self.last_filter = None

    def similarity_search_with_score(self, query, k, filter=None):
        self.last_filter = filter
        if self._error is not None:
            raise self._error
        return self._results


def test_retrieve_passes_metadata_filter_through_to_vectorstore():
    doc = Document(page_content="宽窄巷子历史街区", metadata={"chunk_id": "a"})
    fake_vectorstore = _FakeVectorstore(results=[(doc, 0.1)])
    retriever = HybridRetriever(fake_vectorstore, [doc], k=1)

    retriever.retrieve(
        "宽窄巷子",
        metadata_filter={"$and": [{"city": "成都"}, {"category": "attractions"}]},
    )

    assert fake_vectorstore.last_filter == {"$and": [{"city": "成都"}, {"category": "attractions"}]}


def test_retrieve_degrades_to_bm25_only_when_dense_search_raises():
    doc = Document(page_content="宽窄巷子历史街区", metadata={"chunk_id": "a"})
    other = Document(page_content="武侯祠博物馆", metadata={"chunk_id": "b"})
    fake_vectorstore = _FakeVectorstore(error=ConnectionError("ollama unreachable"))
    retriever = HybridRetriever(fake_vectorstore, [doc, other], k=2)

    result = retriever.retrieve("宽窄巷子")

    assert [item.metadata["chunk_id"] for item in result] == ["a", "b"]


def test_vectorstore_manager_uses_injected_embeddings_instead_of_dashscope(tmp_path):
    from app.rag.vectorstore import VectorStoreManager

    sentinel = object()
    manager = VectorStoreManager(persist_directory=str(tmp_path), embeddings=sentinel)

    assert manager.embeddings is sentinel


def test_get_ollama_embeddings_points_at_local_ollama_openai_compatible_endpoint():
    from app.rag.local_embeddings import get_ollama_embeddings

    embeddings = get_ollama_embeddings()

    assert embeddings.openai_api_base == "http://127.0.0.1:11434/v1"
    assert embeddings.model == "qwen3-embedding:4b"
