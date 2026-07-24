"""本地 Ollama Embedding 客户端：复用 OpenAI 兼容接口，不依赖外部 API Key。"""

from __future__ import annotations

from langchain_openai import OpenAIEmbeddings

OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
OLLAMA_EMBEDDING_MODEL = "qwen3-embedding:4b"
LOCAL_MOCK_COLLECTION = "local_mock_dense"


def get_ollama_embeddings(
    *,
    base_url: str = OLLAMA_BASE_URL,
    model: str = OLLAMA_EMBEDDING_MODEL,
) -> OpenAIEmbeddings:
    """构造指向本地 Ollama 的 embedding 客户端。

    `check_embedding_ctx_length=False` 是必需的：langchain-openai 默认会用
    tiktoken 按已知 OpenAI 模型名做 token 截断，本地模型名不在其列表里会
    直接报错，关闭这项检查后按原始文本发送。
    """
    return OpenAIEmbeddings(
        base_url=base_url,
        model=model,
        api_key="ollama-local-placeholder",
        check_embedding_ctx_length=False,
    )
