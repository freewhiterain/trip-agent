"""
重排序器：使用 LLM 进行重排
"""
from typing import List
from langchain_core.documents import Document
from langchain_community.chat_models import ChatTongyi
from app.config import settings
from app.utils.logger import app_logger


class LLMReranker:
    """LLM 重排序器"""

    def __init__(self, model_name: str = "qwen-turbo"):
        self.llm = ChatTongyi(
            model=model_name,
            api_key=settings.dashscope_api_key,
            temperature=0
        )

    def rerank(self, query: str, documents: List[Document], top_k: int = 3) -> List[Document]:
        """LLM 重排序"""
        if len(documents) <= top_k:
            return documents

        app_logger.info(f"重排序 {len(documents)} 个文档...")

        # 简化版：直接返回前 top_k 个
        # 生产环境应构建 prompt 让 LLM 为每个文档打分（0-10）
        return documents[:top_k]
