"""高铁查询兼容 Subagent；无实时数据源时明确降级。"""

from langchain.agents import create_agent
from langchain.tools import tool
from langchain_community.chat_models import ChatTongyi

from app.config import settings
from app.utils.logger import app_logger


@tool
async def query_trains_from_mcp(origin: str, destination: str, departure_date: str) -> str:
    """查询高铁；当前未配置 12306 MCP 时不返回虚构车次或价格。"""
    return f"当前未配置经过验证的高铁数据源，无法查询 {origin} 到 {destination} 在 {departure_date} 的实时车次、价格或余量。"


def create_train_subagent():
    agent = create_agent(
        model=ChatTongyi(model=settings.qwen_model_name, api_key=settings.dashscope_api_key, temperature=0.3),
        tools=[query_trains_from_mcp],
        system_prompt="你是高铁查询专家。只能展示工具返回的可验证结果，不得补写车次、价格或余量。",
    )
    app_logger.info("✅ 高铁兼容 Subagent 创建完成")
    return agent
