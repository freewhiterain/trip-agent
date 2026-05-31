"""
住宿工具 Subagent
调用 AIGoHotel MCP 服务
"""
import json
from langchain_community.chat_models import ChatTongyi
from langchain.agents import create_agent
from langchain.tools import tool
from app.config import settings
from app.utils.logger import app_logger


@tool
async def search_hotels_from_mcp(
    destination: str,
    check_in_date: str,
    check_out_date: str,
    hotel_type: str = "economy_hotel"
) -> str:
    """
    从 AIGoHotel MCP 查询酒店信息

    参数：
    - destination: 目的地城市
    - check_in_date: 入住日期
    - check_out_date: 退房日期
    - hotel_type: 酒店类型
    """
    app_logger.info(f"查询酒店: {destination}, {check_in_date} ~ {check_out_date}")

    # TODO: 第七章集成 AIGoHotel MCP 后替换
    mock_hotels = [
        {
            "name": f"{destination}精品酒店",
            "type": hotel_type,
            "location": f"{destination}市中心",
            "price_per_night": 280.0,
            "rating": 4.5,
            "amenities": ["免费WiFi", "停车场", "早餐"]
        }
    ]
    return json.dumps(mock_hotels, ensure_ascii=False, indent=2)


def create_hotel_subagent():
    llm = ChatTongyi(
        model=settings.qwen_model_name,
        api_key=settings.dashscope_api_key,
        temperature=0.3
    )
    agent = create_agent(
        model=llm,
        tools=[search_hotels_from_mcp],
        system_prompt="""你是酒店查询专家。
根据目的地、入住日期、退房日期和酒店类型，调用 search_hotels_from_mcp 查询酒店，
展示价格、评分、设施等信息。一定要调用工具，不要编造数据。"""
    )
    app_logger.info("✅ 住宿 Subagent 创建完成")
    return agent
