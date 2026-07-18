"""自驾路线兼容 Subagent；无地图数据源时明确降级。"""

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_community.chat_models import ChatTongyi

from app.config import settings
from app.utils.logger import app_logger


@tool
async def plan_driving_route_from_mcp(origin: str, destination: str) -> str:
    """规划自驾路线；未配置地图 MCP 时不返回虚构距离、时间或费用。"""
    return f"当前未配置经过验证的地图路线数据源，无法计算 {origin} 到 {destination} 的实时路线。"


def create_driving_subagent():
    agent = create_agent(
        model=ChatTongyi(model=settings.qwen_model_name, api_key=settings.dashscope_api_key, temperature=0.3),
        tools=[plan_driving_route_from_mcp],
        system_prompt="你是自驾路线专家。只能展示工具返回的可验证结果，不得补写距离、时间或费用。",
    )
    app_logger.info("✅ 自驾兼容 Subagent 创建完成")
    return agent
