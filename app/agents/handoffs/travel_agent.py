"""
Handoffs 主 Agent
一个 Agent + 中间件实现整个旅行规划流程
"""
import httpx
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from app.config import settings
from app.core.state import TravelState
from app.core.checkpointer import get_checkpointer
from app.core.middleware import create_step_config_middleware
from app.tools.state_transition import (
    record_requirement_tool,
    select_destination_tool,
    select_transport_tool,
    select_accommodation_tool,
    select_food_tool,
    generate_itinerary_tool,
    summarize_budget_tool,
    confirm_plan_draft_tool,
    ALL_ROLLBACK_TOOLS
)
from app.tools.router_query import query_destination_info
from app.tools.transport_query import query_transport_options
from app.utils.logger import app_logger


def get_llm():
    """获取配置好的千问模型（兼容 OpenAI 接口）

    ⚠️ 环境兼容性修复（课件没有）：
       本机开了代理时，openai SDK 内部的 httpx 客户端会读 HTTPS_PROXY，
       即使设了 NO_PROXY 在异步场景也可能不生效。
       这里显式传入不走代理的 httpx 客户端，让千问 API 直连阿里云。
    """
    # 创建强制不走代理的 httpx 客户端
    http_client_sync = httpx.Client(trust_env=False, timeout=60.0)
    http_async_client = httpx.AsyncClient(trust_env=False, timeout=60.0)

    return ChatOpenAI(
        model=settings.qwen_model_name,
        base_url=settings.qwen_base_url,
        api_key=settings.dashscope_api_key,
        temperature=settings.qwen_temperature,
        max_tokens=settings.qwen_max_tokens,
        streaming=True,
        http_client=http_client_sync,
        http_async_client=http_async_client,
    )


async def create_travel_agent():
    """
    创建 Handoffs 旅行规划 Agent

    返回：
        编译好的 Agent（可直接调用）
    """
    app_logger.info("创建 Travel Agent...")

    llm = get_llm()
    step_config_middleware = await create_step_config_middleware()

    all_tools = [
        record_requirement_tool,
        select_destination_tool,
        select_transport_tool,
        select_accommodation_tool,
        select_food_tool,
        generate_itinerary_tool,
        summarize_budget_tool,
        confirm_plan_draft_tool,
        query_destination_info,
        query_transport_options,
        *ALL_ROLLBACK_TOOLS,
    ]

    checkpointer = await get_checkpointer()

    agent = create_agent(
        model=llm,
        tools=all_tools,
        state_schema=TravelState,
        middleware=[step_config_middleware],
        checkpointer=checkpointer,
    )

    app_logger.info("✅ Travel Agent 创建完成")
    return agent
