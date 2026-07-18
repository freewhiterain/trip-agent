"""可离线验证、可选 CrossEncoder 的相关性重排器。"""

from __future__ import annotations

from collections.abc import Callable

import jieba
from langchain_core.documents import Document


ScoreFunction = Callable[[str, list[Document]], list[float]]


class RelevanceReranker:
    """对候选文档实际打分并重排；默认使用中文词项覆盖率。"""

    def __init__(self, scorer: ScoreFunction | None = None):
        self.scorer = scorer or self._lexical_scores

    @staticmethod
    def _lexical_scores(query: str, documents: list[Document]) -> list[float]:
        query_terms = {term.strip().lower() for term in jieba.cut(query) if term.strip()}
        scores = []
        for document in documents:
            terms = {term.strip().lower() for term in jieba.cut(document.page_content) if term.strip()}
            overlap = len(query_terms & terms)
            scores.append(overlap / max(len(query_terms), 1))
        return scores

    def rerank(self, query: str, documents: list[Document], top_k: int = 3) -> list[Document]:
        if not documents:
            return []
        scores = self.scorer(query, documents)
        if len(scores) != len(documents):
            raise ValueError("Reranker 返回的分数数量与文档数量不一致")
        ranked = sorted(
            zip(documents, scores),
            key=lambda item: item[1],
            reverse=True,
        )
        result = []
        for document, score in ranked[:top_k]:
            copied = Document(page_content=document.page_content, metadata=dict(document.metadata))
            copied.metadata["rerank_score"] = float(score)
            result.append(copied)
        return result


class CrossEncoderReranker(RelevanceReranker):
    """按需加载 sentence-transformers CrossEncoder，不在导入时下载模型。"""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        super().__init__(self._cross_encoder_scores)

    def _cross_encoder_scores(self, query: str, documents: list[Document]) -> list[float]:
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
        pairs = [(query, document.page_content) for document in documents]
        return [float(score) for score in self._model.predict(pairs)]
