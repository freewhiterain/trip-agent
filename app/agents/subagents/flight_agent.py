"""
航班查询 Subagent
调用 Aviation MCP 服务
"""
import json
from langchain_community.chat_models import ChatTongyi
from langchain.agents import create_agent
from langchain.tools import tool
from app.config import settings
from app.utils.logger import app_logger


@tool
async def query_flights_from_mcp(origin: str, destination: str, departure_date: str) -> str:
    """
    从 Aviation MCP 查询航班信息

    参数：
    - origin: 出发城市
    - destination: 目的地城市
    - departure_date: 出发日期，格式 YYYY-MM-DD
    """
    app_logger.info(f"查询航班: {origin} -> {destination}, {departure_date}")

    # TODO: 第七章集成 Aviation MCP 后替换
    mock_flights = [
        {
            "flight_number": "CA1234",
            "airline": "中国国航",
            "departure_airport": f"{origin}国际机场",
            "arrival_airport": f"{destination}国际机场",
            "departure_time": f"{departure_date} 08:00",
            "arrival_time": f"{departure_date} 10:30",
            "duration": "2小时30分",
            "price": 800.0,
            "cabin_class": "经济舱",
            "available_seats": 45
        },
        {
            "flight_number": "MU5678",
            "airline": "东方航空",
            "departure_airport": f"{origin}国际机场",
            "arrival_airport": f"{destination}国际机场",
            "departure_time": f"{departure_date} 14:00",
            "arrival_time": f"{departure_date} 16:20",
            "duration": "2小时20分",
            "price": 750.0,
            "cabin_class": "经济舱",
            "available_seats": 23
        }
    ]
    return json.dumps(mock_flights, ensure_ascii=False, indent=2)


def create_flight_subagent():
    llm = ChatTongyi(
        model=settings.qwen_model_name,
        api_key=settings.dashscope_api_key,
        temperature=0.3
    )
    agent = create_agent(
        model=llm,
        tools=[query_flights_from_mcp],
        system_prompt="""你是航班查询专家。
接收出发城市、目的地城市、出发日期，调用 query_flights_from_mcp 查询航班，
按价格排序后用清晰格式展示航班列表。一定要调用工具，不要编造数据。"""
    )
    app_logger.info("✅ 航班 Subagent 创建完成")
    return agent
