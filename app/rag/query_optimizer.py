"""
查询优化模块
包含 Multi-Query、HyDE 策略
"""
from typing import List
from langchain_core.prompts import ChatPromptTemplate
from langchain_community.chat_models import ChatTongyi
from app.config import settings
from app.utils.logger import app_logger

_model = None


def get_model():
    global _model
    if _model is None:
        _model = ChatTongyi(
            model="qwen-turbo",
            api_key=settings.dashscope_api_key,
            temperature=0
        )
    return _model


class MultiQueryOptimizer:
    """Multi-Query 优化器：生成查询的多个变体以提高召回率"""

    def __init__(self, num_variants: int = 3):
        self.num_variants = num_variants
        self.prompt = ChatPromptTemplate.from_template(
            """你是一个查询优化专家。给定一个用户查询，生成 {num} 个语义相似但表述不同的查询变体。

原始查询：{query}

要求：
1. 保持原查询的核心意图
2. 使用不同的词汇和表述方式
3. 考虑同义词、相关概念
4. 每行一个变体，不要编号

变体列表："""
        )

    def optimize(self, query: str) -> List[str]:
        app_logger.info(f"生成查询变体: {query}")
        messages = self.prompt.format_messages(query=query, num=self.num_variants)
        response = get_model().invoke(messages)
        variants = [line.strip() for line in response.content.strip().split('\n') if line.strip()]
        all_queries = [query] + variants[:self.num_variants]
        app_logger.debug(f"生成了 {len(all_queries)} 个查询变体")
        return all_queries


class HyDEOptimizer:
    """HyDE 优化器：生成假设性文档用于检索"""

    def __init__(self):
        self.prompt = ChatPromptTemplate.from_template(
            """请根据以下问题，生成一段假设性的回答文档（200-300字），包含具体景点名称和推荐理由。

问题：{query}

假设性文档："""
        )

    def generate_hypothetical_doc(self, query: str) -> str:
        app_logger.info(f"生成假设性文档: {query}")
        messages = self.prompt.format_messages(query=query)
        response = get_model().invoke(messages)
        return response.content.strip()
