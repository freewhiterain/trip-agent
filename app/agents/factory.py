"""旅行 Agent 运行模式选择与兼容回退。"""

from app.config import settings
from app.utils.logger import app_logger


async def create_chat_agent():
    """根据配置创建聊天 Agent，并在允许时回退到旧规划流程。"""
    mode = settings.travel_agent_mode.strip().lower()

    if mode == "legacy":
        from app.agents.handoffs.travel_agent import create_travel_agent

        return await create_travel_agent()

    if mode == "supervisor":
        try:
            from app.agents.supervisor import create_supervisor_agent

            return await create_supervisor_agent()
        except (ImportError, ModuleNotFoundError):
            if not settings.allow_legacy_fallback:
                raise RuntimeError("Supervisor Agent 尚未可用，且未允许旧流程回退")
            app_logger.warning("Supervisor Agent 尚未可用，回退到只读规划版旧流程")
            from app.agents.handoffs.travel_agent import create_travel_agent

            return await create_travel_agent()

    raise ValueError(f"不支持的 TRAVEL_AGENT_MODE: {settings.travel_agent_mode}")
