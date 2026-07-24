"""RAPTOR 摘要树的简化实现：按 document_id 分组，每组生成一个非 LLM 的占位
摘要 chunk，不做真实的 UMAP/GMM 递归聚类。真实算法升级路径见
docs/superpowers/specs/2026-07-24-rag-heavyweight-capabilities-future-design.md。
本阶段不接入检索主链路，只交付可独立测试的接口。
"""

from __future__ import annotations

from langchain_core.documents import Document

from app.rag.identifiers import stable_hash

SUMMARY_PREVIEW_LENGTH = 80


class RaptorIndexer:
    def build_tree(self, documents: list[Document]) -> list[Document]:
        groups: dict[str, list[Document]] = {}
        for document in documents:
            document_id = str(document.metadata.get("document_id", ""))
            groups.setdefault(document_id, []).append(document)

        summaries: list[Document] = []
        for document_id, group in groups.items():
            if not document_id:
                continue
            preview = group[0].page_content[:SUMMARY_PREVIEW_LENGTH]
            summary_metadata = dict(group[0].metadata)
            summary_metadata["is_raptor_summary"] = True
            summary_metadata["chunk_id"] = stable_hash(document_id, "raptor-summary")
            summaries.append(Document(page_content=preview, metadata=summary_metadata))

        return summaries


def get_raptor_indexer() -> RaptorIndexer:
    return RaptorIndexer()
