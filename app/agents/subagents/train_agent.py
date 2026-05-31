"""
高铁查询 Subagent
调用 12306 MCP 服务
"""
import json
from langchain_community.chat_models import ChatTongyi
from langchain.agents import create_agent
from langchain.tools import tool
from app.config import settings
from app.utils.logger import app_logger


@tool
async def query_trains_from_mcp(origin: str, destination: str, departure_date: str) -> str:
    """
    从 12306 MCP 查询高铁/火车信息

    参数：
    - origin: 出发城市
    - destination: 目的地城市
    - departure_date: 出发日期
    """
    app_logger.info(f"查询高铁: {origin} -> {destination}, {departure_date}")

    # TODO: 第七章集成 12306 MCP 后替换
    mock_trains = [
        {
            "train_number": "G123",
            "departure_station": f"{origin}站",
            "arrival_station": f"{destination}站",
            "departure_time": f"{departure_date} 09:00",
            "arrival_time": f"{departure_date} 13:30",
            "duration": "4小时30分",
            "seat_types": ["商务座", "一等座", "二等座"],
            "prices": {"商务座": 1200.0, "一等座": 650.0, "二等座": 410.0},
            "available": True
        },
        {
            "train_number": "D456",
            "departure_station": f"{origin}站",
            "arrival_station": f"{destination}站",
            "departure_time": f"{departure_date} 11:00",
            "arrival_time": f"{departure_date} 16:10",
            "duration": "5小时10分",
            "seat_types": ["一等座", "二等座"],
            "prices": {"一等座": 480.0, "二等座": 300.0},
            "available": True
        }
    ]
    return json.dumps(mock_trains, ensure_ascii=False, indent=2)


def create_train_subagent():
    llm = ChatTongyi(
        model=settings.qwen_model_name,
        api_key=settings.dashscope_api_key,
        temperature=0.3
    )
    agent = create_agent(
        model=llm,
        tools=[query_trains_from_mcp],
        system_prompt="""你是高铁查询专家。
接收出发城市、目的地城市、出发日期，调用 query_trains_from_mcp 查询车次，
按时间排序后用清晰格式展示各座位类型和价格。一定要调用工具，不要编造数据。"""
    )
    app_logger.info("✅ 高铁 Subagent 创建完成")
    return agent
