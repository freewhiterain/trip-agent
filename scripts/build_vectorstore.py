"""离线构建本地 Dense 向量库：用本地 Ollama embedding 把成都模拟资料转成向量，
写入一个持久化 Chroma collection，供 LocalKnowledgeService 的 Dense 检索加载。

不在 FastAPI 请求路径上运行，可重复执行（覆盖同一 collection）。运行前需先
启动本地 Ollama 并确保已拉取 qwen3-embedding:4b 模型：
    ollama pull qwen3-embedding:4b
运行方式：
    python scripts/build_vectorstore.py
"""

from __future__ import annotations

from app.rag.document_loader import DocumentManager
from app.rag.local_embeddings import LOCAL_MOCK_COLLECTION, get_ollama_embeddings
from app.rag.text_splitter import ParentDocumentSplitter
from app.rag.vectorstore import VectorStoreManager
from app.utils.logger import app_logger


def build_vectorstore(
    *,
    document_manager: DocumentManager | None = None,
    persist_directory: str = "data/vectorstore",
) -> None:
    document_manager = document_manager or DocumentManager()
    documents = document_manager.load_all_documents()
    if not documents:
        app_logger.warning("未找到文档，跳过向量库构建。")
        return

    _, children = ParentDocumentSplitter().split_documents(documents)
    if not children:
        app_logger.warning("切分后没有子文档，跳过向量库构建。")
        return

    manager = VectorStoreManager(
        persist_directory=persist_directory,
        collection_name=LOCAL_MOCK_COLLECTION,
        embeddings=get_ollama_embeddings(),
    )
    manager.create_vectorstore(children)
    app_logger.info(
        f"✅ 本地向量库构建完成：{len(children)} 个子文档，"
        f"collection={LOCAL_MOCK_COLLECTION}，目录={persist_directory}"
    )


def main() -> None:
    build_vectorstore()


if __name__ == "__main__":
    main()
