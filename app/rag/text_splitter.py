"""
文本切分：父文档 + 子文档策略
"""
from typing import List, Tuple
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.utils.logger import app_logger
from app.rag.identifiers import document_id, stable_hash


class ParentDocumentSplitter:
    """
    父文档切分器

    策略：
    - 父文档：1000 字符/块（用于最终上下文）
    - 子文档：200 字符/块（用于向量检索）
    """

    def __init__(
            self,
            parent_chunk_size: int = 1000,
            parent_chunk_overlap: int = 200,
            child_chunk_size: int = 200,
            child_chunk_overlap: int = 50
    ):
        self.parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=parent_chunk_size,
            chunk_overlap=parent_chunk_overlap,
            separators=["\n\n", "\n", "。", "，", " ", ""]
        )
        self.child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=child_chunk_size,
            chunk_overlap=child_chunk_overlap,
            separators=["\n\n", "\n", "。", "，", " ", ""]
        )

    def split_documents(self, documents: List[Document]) -> Tuple[List[Document], List[Document]]:
        """
        切分文档为父文档和子文档

        返回：
        - parent_docs: 父文档列表
        - child_docs: 子文档列表（包含 parent_id）
        """
        parent_docs = []
        child_docs = []

        for doc in documents:
            doc_id = document_id(doc)
            parent_chunks = self.parent_splitter.split_documents([doc])

            for i, parent_chunk in enumerate(parent_chunks):
                parent_id = stable_hash(doc_id, "parent", i)
                parent_chunk.metadata["document_id"] = doc_id
                parent_chunk.metadata["parent_id"] = parent_id
                parent_docs.append(parent_chunk)

                child_chunks = self.child_splitter.split_documents([parent_chunk])
                for child_index, child_chunk in enumerate(child_chunks):
                    child_chunk.metadata["document_id"] = doc_id
                    child_chunk.metadata["parent_id"] = parent_id
                    child_chunk.metadata["chunk_id"] = stable_hash(
                        parent_id,
                        "child",
                        child_index,
                        child_chunk.page_content,
                    )
                    child_docs.append(child_chunk)

        app_logger.info(f"切分完成: {len(parent_docs)} 个父文档, {len(child_docs)} 个子文档")
        return parent_docs, child_docs
