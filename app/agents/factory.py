"""旅行 Agent 运行模式选择。"""

from app.config import settings


async def create_chat_agent():
    """创建规划 Agent；旧 Handoffs 流程已退役,统一走 Supervisor。"""
    mode = settings.travel_agent_mode.strip().lower()
    if mode != "supervisor":
        raise ValueError(
            f"TRAVEL_AGENT_MODE={settings.travel_agent_mode} 已不受支持,"
            "旧 Handoffs 流程已由对话协调器 + Supervisor 取代,请使用 supervisor 模式。"
        )
    from app.agents.supervisor import create_supervisor_agent

    return await create_supervisor_agent()
