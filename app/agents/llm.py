"""共享的千问 LLM 构造入口。"""

import httpx
from langchain_openai import ChatOpenAI

from app.config import settings


def get_llm(temperature: float | None = None) -> ChatOpenAI:
    """获取配置好的千问模型（兼容 OpenAI 接口）。

    环境兼容性说明：本机开代理时,openai SDK 内部的 httpx 客户端会读 HTTPS_PROXY,
    即使设了 NO_PROXY 在异步场景也可能不生效,因此显式传入不走代理的 httpx 客户端。
    """
    http_client_sync = httpx.Client(trust_env=False, timeout=60.0)
    http_async_client = httpx.AsyncClient(trust_env=False, timeout=60.0)

    return ChatOpenAI(
        model=settings.qwen_model_name,
        base_url=settings.qwen_base_url,
        api_key=settings.dashscope_api_key,
        temperature=settings.qwen_temperature if temperature is None else temperature,
        max_tokens=settings.qwen_max_tokens,
        streaming=True,
        http_client=http_client_sync,
        http_async_client=http_async_client,
    )
