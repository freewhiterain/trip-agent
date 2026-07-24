from langchain_core.documents import Document

from app.agents.workers.local_knowledge import LocalKnowledgeService


class _RecordingVectorstore:
    def __init__(self):
        self.calls: list[dict] = []

    def similarity_search_with_score(self, query, k, filter=None):
        self.calls.append({"query": query, "filter": filter})
        return []


def test_search_destination_filters_dense_search_by_city_and_category():
    documents = [
        Document(
            page_content="位于成华区。是熊猫文化主题下的代表性自然教育地点。",
            metadata={"city": "成都", "category": "attractions", "chunk_id": "panda"},
        ),
    ]
    vectorstore = _RecordingVectorstore()
    service = LocalKnowledgeService(documents=documents, vectorstore=vectorstore)

    service.search_destination("成都", "attractions", "熊猫基地")

    assert vectorstore.calls
    assert vectorstore.calls[0]["filter"] == {"$and": [{"city": "成都"}, {"category": "attractions"}]}


def test_search_destination_strips_but_does_not_casefold_dense_metadata_filter():
    documents = [
        Document(
            page_content="位于成华区。是熊猫文化主题下的代表性自然教育地点。",
            metadata={"city": "成都", "category": "attractions", "chunk_id": "panda"},
        ),
    ]
    vectorstore = _RecordingVectorstore()
    service = LocalKnowledgeService(documents=documents, vectorstore=vectorstore)

    service.search_destination(" 成都 ", " attractions ", "熊猫基地")

    assert vectorstore.calls
    assert vectorstore.calls[0]["filter"] == {"$and": [{"city": "成都"}, {"category": "attractions"}]}


def test_service_reuses_the_same_vectorstore_instance_across_queries_without_rebuilding():
    documents = [
        Document(
            page_content="位于成华区。",
            metadata={"city": "成都", "category": "attractions", "chunk_id": "panda"},
        ),
        Document(
            page_content="位于青羊区。",
            metadata={"city": "成都", "category": "hotel", "chunk_id": "hotel"},
        ),
    ]
    vectorstore = _RecordingVectorstore()
    service = LocalKnowledgeService(documents=documents, vectorstore=vectorstore)

    service.search_destination("成都", "attractions", "熊猫")
    service.search_destination("成都", "hotel", "住宿")

    assert service.vectorstore is vectorstore
    assert len(vectorstore.calls) == 2
