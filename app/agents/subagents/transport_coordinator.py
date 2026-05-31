"""
交通规划协调器（主 Agent）
使用 Subagents 模式
（按课件第 5 章 2.3 节实现）
"""
from langchain_community.chat_models import ChatTongyi
from langchain.agents import create_agent
from langchain.tools import tool
from app.config import settings
from app.agents.subagents.flight_agent import create_flight_subagent
from app.agents.subagents.train_agent import create_train_subagent
from app.agents.subagents.driving_agent import create_driving_subagent
from app.utils.logger import app_logger


# ============== 创建 Subagents ==============

flight_subagent = create_flight_subagent()
train_subagent = create_train_subagent()
driving_subagent = create_driving_subagent()


# ============== 将 Subagents 包装为 Tools ==============

@tool("query_flights", description="查询航班信息。需要提供出发城市、目的地城市、出发日期和乘客数量。")
async def query_flights_tool(
    origin: str,
    destination: str,
    departure_date: str,
    passenger_count: int = 1
) -> str:
    """
    查询航班信息（调用航班 Subagent）
    """

    app_logger.info(f"🔧 调用航班 Subagent: {origin} -> {destination}")

    # 调用航班 Subagent
    result = await flight_subagent.ainvoke({
        "messages": [
            {
                "role": "user",
                "content": f"请查询从 {origin} 到 {destination} 的航班，出发日期是 {departure_date}，共 {passenger_count} 人。"
            }
        ]
    })

    # 返回 Subagent 的最终消息
    return result["messages"][-1].content


@tool("query_trains", description="查询高铁/火车信息。需要提供出发城市、目的地城市、出发日期。")
async def query_trains_tool(
    origin: str,
    destination: str,
    departure_date: str
) -> str:
    """
    查询高铁信息（调用高铁 Subagent）
    """

    app_logger.info(f"🔧 调用高铁 Subagent: {origin} -> {destination}")

    result = await train_subagent.ainvoke({
        "messages": [
            {
                "role": "user",
                "content": f"请查询从 {origin} 到 {destination} 的高铁，出发日期是 {departure_date}。"
            }
        ]
    })

    return result["messages"][-1].content


@tool("plan_driving_route", description="规划自驾路线。需要提供出发城市、目的地城市。")
async def plan_driving_route_tool(
    origin: str,
    destination: str
) -> str:
    """
    规划自驾路线（调用自驾 Subagent）
    """

    app_logger.info(f"🔧 调用自驾 Subagent: {origin} -> {destination}")

    result = await driving_subagent.ainvoke({
        "messages": [
            {
                "role": "user",
                "content": f"请规划从 {origin} 到 {destination} 的自驾路线。"
            }
        ]
    })

    return result["messages"][-1].content


# ============== 创建交通规划主 Agent ==============

def create_transport_coordinator():
    """
    创建交通规划协调器（主 Agent）

    这个 Agent 拥有三个工具（封装的 Subagents）：
    - query_flights：查询航班
    - query_trains：查询高铁
    - plan_driving_route：规划自驾路线

    主 Agent 会根据用户需求动态决定调用哪个工具。
    """

    llm = ChatTongyi(
        model=settings.qwen_model_name,
        api_key=settings.dashscope_api_key,
        temperature=0.7
    )

    coordinator = create_agent(
        model=llm,
        tools=[
            query_flights_tool,
            query_trains_tool,
            plan_driving_route_tool
        ],
        system_prompt="""你是交通规划协调专家。

**可用工具**：
1. query_flights：查询航班信息（适合长途旅行，速度快但价格较高）
2. query_trains：查询高铁信息（适合中短途，舒适便捷）
3. plan_driving_route：规划自驾路线（适合深度游，自由灵活）

**工作流程**：
1. 理解用户的交通需求（出发地、目的地、日期、人数）
2. 根据距离和用户偏好，推荐合适的交通方式
3. 调用对应的工具查询详细信息
4. 整合工具返回的结果，以友好的方式展示给用户
5. 帮助用户对比不同方案的优劣

**注意事项**：
- 如果用户明确指定交通方式，直接调用对应工具
- 如果用户未指定，根据距离推荐：
  * < 300km：推荐高铁
  * 300-1000km：推荐高铁或航班
  * > 1000km：推荐航班
- 自驾适合目的地景点分散的情况
- 调用工具后，用清晰的格式展示结果
- 可以主动询问用户偏好（时间优先还是价格优先）
"""
    )

    app_logger.info("✅ 交通规划协调器（主 Agent）创建完成")

    return coordinator
