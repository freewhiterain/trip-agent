"""
混合检索器：BM25 + Dense + RRF 融合
"""
from typing import List, Tuple
from langchain_core.documents import Document
from langchain_chroma import Chroma
from rank_bm25 import BM25Okapi
import jieba
from app.utils.logger import app_logger
from app.rag.identifiers import chunk_id
from app.rag.reranker import RelevanceReranker


class HybridRetriever:
    """
    混合检索器

    结合：
    - BM25（关键词匹配）
    - Dense（语义相似度）
    - RRF（倒数排名融合）
    """

    def __init__(
            self,
            vectorstore: Chroma | None,
            documents: List[Document],
            k: int = 5,
            parent_documents: List[Document] | None = None,
            reranker: RelevanceReranker | None = None,
    ):
        self.vectorstore = vectorstore
        self.documents = documents
        self.k = k
        self.parent_documents = {
            str(doc.metadata.get("parent_id")): doc
            for doc in (parent_documents or [])
            if doc.metadata.get("parent_id")
        }
        self.reranker = reranker
        self._init_bm25()

    def _init_bm25(self):
        """初始化 BM25 索引"""
        app_logger.info("初始化 BM25 索引...")
        tokenized_docs = [list(jieba.cut(doc.page_content)) for doc in self.documents]
        self.bm25 = BM25Okapi(tokenized_docs)
        app_logger.info("✅ BM25 索引初始化完成")

    def retrieve(self, query: str) -> List[Document]:
        """
        混合检索

        流程：
        1. BM25 检索 top-k
        2. Dense 检索 top-k
        3. RRF 融合
        4. 返回融合后的 top-k
        """
        # BM25 检索
        query_tokens = list(jieba.cut(query))
        bm25_scores = self.bm25.get_scores(query_tokens)
        bm25_top_indices = sorted(
            range(len(bm25_scores)),
            key=lambda i: bm25_scores[i],
            reverse=True
        )[:self.k * 2]
        bm25_docs = [(self.documents[i], bm25_scores[i]) for i in bm25_top_indices]
        app_logger.debug(f"BM25 检索到 {len(bm25_docs)} 个候选")

        # Dense 检索
        dense_docs = (
            self.vectorstore.similarity_search_with_score(query, k=self.k * 2)
            if self.vectorstore is not None
            else []
        )
        app_logger.debug(f"Dense 检索到 {len(dense_docs)} 个候选")

        # RRF 融合
        fused_docs = self._rrf_fusion(bm25_docs, dense_docs, k=60)
        app_logger.info(f"✅ 混合检索完成，返回 {len(fused_docs)} 个结果")
        if self.reranker:
            fused_docs = self.reranker.rerank(query, fused_docs, top_k=self.k)
        return self._resolve_parent_documents(fused_docs)

    def _rrf_fusion(
            self,
            bm25_docs: List[Tuple[Document, float]],
            dense_docs: List[Tuple[Document, float]],
            k: int = 60
    ) -> List[Document]:
        """倒数排名融合（RRF）"""
        scores = {}

        for rank, (doc, _) in enumerate(bm25_docs, 1):
            doc_id = chunk_id(doc)
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)

        for rank, (doc, _) in enumerate(dense_docs, 1):
            doc_id = chunk_id(doc)
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)

        all_docs = {chunk_id(doc): doc for doc, _ in bm25_docs + dense_docs}
        sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [all_docs[doc_id] for doc_id, _ in sorted_docs[:self.k]]

    def _resolve_parent_documents(self, documents: List[Document]) -> List[Document]:
        if not self.parent_documents:
            return documents[:self.k]

        resolved = []
        seen = set()
        for document in documents:
            parent_id = str(document.metadata.get("parent_id", ""))
            parent = self.parent_documents.get(parent_id, document)
            key = chunk_id(parent)
            if key not in seen:
                resolved.append(parent)
                seen.add(key)
        return resolved[:self.k]
