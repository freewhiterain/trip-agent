"""
自驾路线规划 Subagent
调用高德地图 MCP 服务
"""
import json
from langchain_community.chat_models import ChatTongyi
from langchain.agents import create_agent
from langchain.tools import tool
from app.config import settings
from app.utils.logger import app_logger


@tool
async def plan_driving_route_from_mcp(origin: str, destination: str) -> str:
    """
    从高德地图 MCP 规划自驾路线

    参数：
    - origin: 出发城市
    - destination: 目的地城市
    """
    app_logger.info(f"规划自驾路线: {origin} -> {destination}")

    # TODO: 第七章集成高德地图 MCP 后替换
    mock_routes = [
        {
            "route_name": "推荐路线（高速优先）",
            "distance": "约 1200 公里",
            "duration": "约 12 小时",
            "toll_fee": 450.0,
            "fuel_cost": 600.0,
            "waypoints": ["途经城市1", "途经城市2", "途经城市3"]
        },
        {
            "route_name": "省钱路线（国道优先）",
            "distance": "约 1250 公里",
            "duration": "约 15 小时",
            "toll_fee": 200.0,
            "fuel_cost": 650.0,
            "waypoints": ["途经城市A", "途经城市B"]
        }
    ]
    return json.dumps(mock_routes, ensure_ascii=False, indent=2)


def create_driving_subagent():
    llm = ChatTongyi(
        model=settings.qwen_model_name,
        api_key=settings.dashscope_api_key,
        temperature=0.3
    )
    agent = create_agent(
        model=llm,
        tools=[plan_driving_route_from_mcp],
        system_prompt="""你是自驾路线规划专家。
接收出发城市和目的地城市，调用 plan_driving_route_from_mcp 规划路线，
提供多个方案对比（时间优先/费用优先），用清晰格式展示距离、时长、费用。一定要调用工具。"""
    )
    app_logger.info("✅ 自驾 Subagent 创建完成")
    return agent
