"""
初始化 RAG 系统
运行方式：python scripts/init_rag.py

前提：
1. 已配置 DASHSCOPE_API_KEY（用于 Embedding）
2. data/documents/ 目录下有 .md 文档
"""
import asyncio
import sys
import os

# === 兼容性修复（课件没有）：让脚本能直接运行 ===
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# === 修复结束 ===

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.rag.document_loader import DocumentManager
from app.rag.text_splitter import ParentDocumentSplitter
from app.rag.vectorstore import VectorStoreManager
from app.utils.logger import app_logger


async def main():
    app_logger.info("开始初始化 RAG 系统...")

    doc_manager = DocumentManager()
    documents = doc_manager.load_all_documents()

    if not documents:
        app_logger.error("未找到文档，请先添加文档到 data/documents/ 目录")
        return

    splitter = ParentDocumentSplitter()
    parent_docs, child_docs = splitter.split_documents(documents)

    vs_manager = VectorStoreManager()
    vs_manager.create_vectorstore(child_docs)

    app_logger.info("🎉 RAG 系统初始化完成！")
    app_logger.info(f"   - 文档数量：{len(documents)}")
    app_logger.info(f"   - 父文档数量：{len(parent_docs)}")
    app_logger.info(f"   - 子文档数量：{len(child_docs)}")


if __name__ == "__main__":
    asyncio.run(main())
