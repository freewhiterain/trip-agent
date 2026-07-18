"""本地静态知识的 Hybrid RAG 查询入口。"""

from functools import lru_cache

from app.rag.document_loader import DocumentManager
from app.rag.evidence import evidence_from_document
from app.rag.reranker import RelevanceReranker
from app.rag.retriever import HybridRetriever
from app.rag.text_splitter import ParentDocumentSplitter
from app.schemas.planning import Evidence


class LocalKnowledgeService:
    def __init__(self):
        documents = DocumentManager().load_all_documents()
        parents, children = ParentDocumentSplitter().split_documents(documents)
        self.retriever = HybridRetriever(
            vectorstore=None,
            documents=children,
            parent_documents=parents,
            reranker=RelevanceReranker(),
            k=4,
        )

    def search(self, query: str) -> list[Evidence]:
        return [evidence_from_document(document) for document in self.retriever.retrieve(query)]


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
