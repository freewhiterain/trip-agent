"""
文本切分：Markdown 标题感知 + 父文档 + 子文档策略
"""
import re
from typing import List, Tuple
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.utils.logger import app_logger
from app.rag.identifiers import document_id, stable_hash


HEADING_PATTERN = re.compile(r"^### (.+)$", re.MULTILINE)


def _segment_by_heading(text: str) -> List[Tuple[str, str]]:
    """按 `### 标题` 切分为 (section_title, section_text) 列表。

    没有匹配到任何三级标题时，整段文本作为一个 section_title="" 的小节。
    """
    matches = list(HEADING_PATTERN.finditer(text))
    if not matches:
        return [("", text)]

    sections: List[Tuple[str, str]] = []
    if matches[0].start() > 0:
        sections.append(("", text[: matches[0].start()]))

    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((title, text[start:end]))

    return sections


def _merge_lone_headings(sections: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """把只有标题、没有正文的孤立小节合并进下一个小节，避免产生空内容 chunk。"""
    merged: List[Tuple[str, str]] = []
    pending_prefix = ""
    for title, body in sections:
        content_without_heading = HEADING_PATTERN.sub("", body, count=1).strip()
        if not content_without_heading:
            pending_prefix += body if body.endswith("\n") else body + "\n"
            continue
        merged.append((title, pending_prefix + body))
        pending_prefix = ""
    if pending_prefix:
        if merged:
            last_title, last_body = merged[-1]
            merged[-1] = (last_title, last_body + pending_prefix)
        else:
            merged.append(("", pending_prefix))
    return merged


class ParentDocumentSplitter:
    """
    父文档切分器

    策略：
    - 先按 Markdown 三级标题（`### 标题`）切成小节，孤立标题自动并入下一节
    - 每个小节内部：父文档 1000 字符/块，子文档 200 字符/块
    - 每个 chunk 携带 metadata["section_title"]，供查询加权使用
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
        - child_docs: 子文档列表（包含 parent_id、section_title）
        """
        parent_docs = []
        child_docs = []

        for doc in documents:
            doc_id = document_id(doc)
            sections = _merge_lone_headings(_segment_by_heading(doc.page_content))

            parent_index = 0
            for section_title, section_text in sections:
                section_doc = Document(page_content=section_text, metadata=dict(doc.metadata))
                parent_chunks = self.parent_splitter.split_documents([section_doc])

                for parent_chunk in parent_chunks:
                    parent_id = stable_hash(doc_id, "parent", parent_index)
                    parent_index += 1
                    parent_chunk.metadata["document_id"] = doc_id
                    parent_chunk.metadata["parent_id"] = parent_id
                    parent_chunk.metadata["section_title"] = section_title
                    parent_docs.append(parent_chunk)

                    child_chunks = self.child_splitter.split_documents([parent_chunk])
                    for child_index, child_chunk in enumerate(child_chunks):
                        child_chunk.metadata["document_id"] = doc_id
                        child_chunk.metadata["parent_id"] = parent_id
                        child_chunk.metadata["section_title"] = section_title
                        child_chunk.metadata["chunk_id"] = stable_hash(
                            parent_id,
                            "child",
                            child_index,
                            child_chunk.page_content,
                        )
                        child_docs.append(child_chunk)

        app_logger.info(f"切分完成: {len(parent_docs)} 个父文档, {len(child_docs)} 个子文档")
        return parent_docs, child_docs
