"""
文档加载与预处理
"""
from pathlib import Path
from typing import List
from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_core.documents import Document
from app.utils.logger import app_logger
from app.rag.identifiers import document_id


MOCK_DOCUMENT_CATEGORIES = {
    "attractions": "attractions",
    "weather": "weather",
    "transport": "transport",
    "accommodation": "hotel",
    "food": "food",
}


class DocumentManager:
    """文档管理器"""

    def __init__(self, base_dir: str = None):
        if base_dir is None:
            project_root = Path(__file__).parent.parent.parent
            self.base_dir = project_root / "data" / "documents"
        else:
            self.base_dir = Path(base_dir)

    def load_destination_documents(self) -> List[Document]:
        """加载所有目的地文档"""
        destinations_dir = self.base_dir / "destinations"

        if not destinations_dir.exists():
            app_logger.warning(f"目的地文档目录不存在: {destinations_dir}")
            return []

        loader = DirectoryLoader(
            str(destinations_dir),
            glob="**/*.md",
            loader_cls=TextLoader,
            loader_kwargs={"encoding": "utf-8", "autodetect_encoding": True}
        )
        documents = loader.load()
        app_logger.info(f"加载了 {len(documents)} 个目的地文档")

        for doc in documents:
            doc.metadata["source_type"] = "destination_guide"
            doc.metadata["category"] = "destinations"
            doc.metadata["document_id"] = document_id(doc)

        return documents

    def load_food_documents(self) -> List[Document]:
        """加载美食文档"""
        food_dir = self.base_dir / "food"
        if not food_dir.exists():
            return []

        loader = DirectoryLoader(
            str(food_dir),
            glob="**/*.md",
            loader_cls=TextLoader,
            loader_kwargs={"encoding": "utf-8", "autodetect_encoding": True}
        )
        documents = loader.load()
        for doc in documents:
            doc.metadata["source_type"] = "food_guide"
            doc.metadata["category"] = "food"
            self._apply_mock_metadata(doc)
            doc.metadata["document_id"] = document_id(doc)
        return documents

    def load_accommodation_documents(self) -> List[Document]:
        """加载住宿文档"""
        acc_dir = self.base_dir / "accommodation"
        if not acc_dir.exists():
            return []

        loader = DirectoryLoader(
            str(acc_dir),
            glob="**/*.md",
            loader_cls=TextLoader,
            loader_kwargs={"encoding": "utf-8", "autodetect_encoding": True}
        )
        documents = loader.load()
        for doc in documents:
            doc.metadata["source_type"] = "accommodation_guide"
            doc.metadata["category"] = "accommodation"
            self._apply_mock_metadata(doc)
            doc.metadata["document_id"] = document_id(doc)
        return documents

    def load_mock_documents(self) -> List[Document]:
        """Load mock worker documents that do not have dedicated loaders."""
        documents = []
        for directory in ("attractions", "weather", "transport"):
            document_dir = self.base_dir / directory
            if not document_dir.exists():
                continue

            loader = DirectoryLoader(
                str(document_dir),
                glob="**/*.md",
                loader_cls=TextLoader,
                loader_kwargs={"encoding": "utf-8", "autodetect_encoding": True}
            )
            for doc in loader.load():
                self._apply_mock_metadata(doc)
                doc.metadata["document_id"] = document_id(doc)
                documents.append(doc)
        return documents

    @staticmethod
    def _apply_mock_metadata(doc: Document) -> None:
        source = Path(str(doc.metadata.get("source", "")))
        category = MOCK_DOCUMENT_CATEGORIES.get(source.parent.name)
        if source.name == "chengdu.md" and category:
            doc.metadata["city"] = "成都"
            doc.metadata["source_type"] = "mock_markdown"
            doc.metadata["category"] = category

    def load_all_documents(self) -> List[Document]:
        """加载所有文档"""
        all_docs = []
        all_docs.extend(self.load_destination_documents())
        all_docs.extend(self.load_food_documents())
        all_docs.extend(self.load_accommodation_documents())
        all_docs.extend(self.load_mock_documents())
        app_logger.info(f"共加载 {len(all_docs)} 个文档")
        return all_docs
