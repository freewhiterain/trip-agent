"""住宿查询兼容 Subagent；无实时数据源时明确降级。"""

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_community.chat_models import ChatTongyi

from app.config import settings
from app.utils.logger import app_logger


@tool
async def search_hotels_from_mcp(destination: str, check_in_date: str, check_out_date: str, hotel_type: str = "economy_hotel") -> str:
    """查询住宿；未配置酒店 MCP 时不返回虚构价格、评分或库存。"""
    return f"当前未配置经过验证的酒店数据源，无法查询 {destination} 从 {check_in_date} 到 {check_out_date} 的 {hotel_type} 实时价格或库存。"


def create_hotel_subagent():
    agent = create_agent(
        model=ChatTongyi(model=settings.qwen_model_name, api_key=settings.dashscope_api_key, temperature=0.3),
        tools=[search_hotels_from_mcp],
        system_prompt="你是住宿查询专家。只能展示工具返回的可验证结果，不得补写酒店价格、评分或库存。",
    )
    app_logger.info("✅ 住宿兼容 Subagent 创建完成")
    return agent
