"""
交通查询工具
调用交通规划协调器（Subagents 主 Agent）
"""
from langchain.tools import tool
from app.utils.logger import app_logger


@tool
async def query_transport_options(
    origin_city: str,
    destination_city: str,
    departure_date: str,
    transport_type: str = None,
    passenger_count: int = 1
) -> str:
    """
    查询交通选项（调用交通规划协调器）

    参数说明：
    - origin_city: 出发城市
    - destination_city: 目的地城市
    - departure_date: 出发日期，格式 YYYY-MM-DD
    - transport_type: 交通方式（可选），可选值：flight、train、driving
    - passenger_count: 乘客人数（可选）
    """
    app_logger.info("调用交通规划协调器")

    from app.agents.subagents.transport_coordinator import create_transport_coordinator

    coordinator = create_transport_coordinator()

    if transport_type:
        type_labels = {"flight": "航班", "train": "高铁", "driving": "自驾"}
        user_query = (
            f"我想从 {origin_city} 去 {destination_city}，"
            f"出发日期是 {departure_date}，"
            f"共 {passenger_count} 人，"
            f"交通方式选择 {type_labels.get(transport_type, transport_type)}，"
            f"请帮我查询详细信息。"
        )
    else:
        user_query = (
            f"我想从 {origin_city} 去 {destination_city}，"
            f"出发日期是 {departure_date}，"
            f"共 {passenger_count} 人，"
            f"请推荐合适的交通方式并提供详细信息。"
        )

    result = await coordinator.ainvoke({
        "messages": [{"role": "user", "content": user_query}]
    })

    return result["messages"][-1].content
