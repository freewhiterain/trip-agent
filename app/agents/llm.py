"""共享的千问 LLM 构造入口。"""

import httpx
from langchain_openai import ChatOpenAI

from app.config import settings


# 跨调用复用的 httpx 客户端。get_llm 在每一次对话轮次都会被调用
# （main_agent 路由、planning 抽取、open_qa、supervisor 行程合成），
# 原先每次都新建一对客户端且从不 close，连接池随请求量单调增长直到
# 耗尽文件描述符。httpx 客户端本身可并发复用，连接池正是为此设计的。
_http_client: httpx.Client | None = None
_http_async_client: httpx.AsyncClient | None = None


def _shared_http_clients() -> tuple[httpx.Client, httpx.AsyncClient]:
    """惰性创建并复用同一对 httpx 客户端。

    环境兼容性说明：本机开代理时,openai SDK 内部的 httpx 客户端会读 HTTPS_PROXY,
    即使设了 NO_PROXY 在异步场景也可能不生效,因此显式传入不走代理的 httpx 客户端。
    """
    global _http_client, _http_async_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.Client(trust_env=False, timeout=60.0)
    if _http_async_client is None or _http_async_client.is_closed:
        _http_async_client = httpx.AsyncClient(trust_env=False, timeout=60.0)
    return _http_client, _http_async_client


def reset_http_clients() -> None:
    """关闭并丢弃共享客户端。供应用关闭和测试隔离使用。

    异步客户端的 aclose 需要事件循环，这里只做同步 close；未关闭的异步
    连接会在进程退出时由 GC 回收，不影响运行期的池复用。
    """
    global _http_client, _http_async_client
    if _http_client is not None and not _http_client.is_closed:
        _http_client.close()
    _http_client = None
    _http_async_client = None


async def aclose_http_clients() -> None:
    """在事件循环内彻底关闭共享客户端，供 FastAPI lifespan 收尾调用。"""
    global _http_client, _http_async_client
    if _http_async_client is not None and not _http_async_client.is_closed:
        await _http_async_client.aclose()
    _http_async_client = None
    if _http_client is not None and not _http_client.is_closed:
        _http_client.close()
    _http_client = None


def get_llm(temperature: float | None = None) -> ChatOpenAI:
    """获取配置好的千问模型（兼容 OpenAI 接口）。

    每次返回新的 ChatOpenAI（temperature 等参数按调用方变化），但底层
    httpx 客户端是共享的。
    """
    http_client_sync, http_async_client = _shared_http_clients()

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
