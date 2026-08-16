"""本地 Ollama Embedding 客户端：复用 OpenAI 兼容接口，不依赖外部 API Key。"""

from __future__ import annotations

from langchain_openai import OpenAIEmbeddings

from app.config import settings

# 与 Settings 的默认值保持一致，供不读配置的调用方直接引用。
OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
OLLAMA_EMBEDDING_MODEL = "qwen3-embedding:4b"
LOCAL_MOCK_COLLECTION = "local_mock_dense"


def get_ollama_embeddings(
    *,
    base_url: str | None = None,
    model: str | None = None,
) -> OpenAIEmbeddings:
    """构造指向本地 Ollama 的 embedding 客户端。

    `check_embedding_ctx_length=False` 是必需的：langchain-openai 默认会用
    tiktoken 按已知 OpenAI 模型名做 token 截断，本地模型名不在其列表里会
    直接报错，关闭这项检查后按原始文本发送。

    地址和模型默认读配置，便于把 Ollama 部署到别的机器或换模型；显式传参
    仍然优先，测试可以不经配置直接构造。
    """
    return OpenAIEmbeddings(
        base_url=base_url or settings.embedding_base_url,
        model=model or settings.embedding_model,
        api_key="ollama-local-placeholder",
        check_embedding_ctx_length=False,
    )
