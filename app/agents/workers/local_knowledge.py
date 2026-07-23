"""本地静态知识的 Hybrid RAG 查询入口。"""

from functools import lru_cache

from langchain_core.documents import Document

from app.rag.document_loader import DocumentManager
from app.rag.evidence import evidence_from_document
from app.rag.reranker import RelevanceReranker
from app.rag.retriever import HybridRetriever
from app.rag.text_splitter import ParentDocumentSplitter
from app.schemas.planning import Evidence, TaskType


class LocalKnowledgeService:
    def __init__(self, documents: list[Document] | None = None):
        self.documents = documents if documents is not None else DocumentManager().load_all_documents()
        self.retriever = self._build_retriever(self.documents)

    @staticmethod
    def _build_retriever(documents: list[Document]) -> HybridRetriever | None:
        parents, children = ParentDocumentSplitter().split_documents(documents)
        if not children:
            return None
        return HybridRetriever(
            vectorstore=None,
            documents=children,
            parent_documents=parents,
            reranker=RelevanceReranker(),
            k=4,
        )

    def search(self, query: str) -> list[Evidence]:
        if self.retriever is None:
            return []
        return [evidence_from_document(document) for document in self.retriever.retrieve(query)]

    def search_destination(
        self,
        destination: str,
        category: TaskType,
        query: str,
    ) -> list[Evidence]:
        normalized_destination = destination.strip().casefold()
        normalized_category = category.strip().casefold()
        documents = [
            document
            for document in self.documents
            if str(document.metadata.get("city", "")).strip().casefold() == normalized_destination
            and str(document.metadata.get("category", "")).strip().casefold() == normalized_category
        ]
        if not documents:
            return []

        retrieval_query = f"{destination} {category} {query}"
        retriever = self._build_retriever(documents)
        if retriever is None:
            return []
        return [evidence_from_document(document) for document in retriever.retrieve(retrieval_query)]


@lru_cache(maxsize=1)
def get_local_knowledge_service() -> LocalKnowledgeService:
    return LocalKnowledgeService()


def load_destination_evidence(destination: str, topic: str) -> list[Evidence]:
    items = get_local_knowledge_service().search(f"{destination} {topic}")
    return [
        item
        for item in items
        if destination in item.content or destination in item.source
    ]
