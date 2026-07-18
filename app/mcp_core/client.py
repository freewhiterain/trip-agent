"""
MCP 客户端管理器
管理所有 MCP 服务连接
"""
import asyncio
from typing import Optional
from app.utils.logger import app_logger


class MCPClientManager:
    """MCP 客户端管理器（单例模式）"""

    _instance: Optional['MCPClientManager'] = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.client = None

    @classmethod
    async def get_instance(cls) -> 'MCPClientManager':
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    await cls._instance.initialize()
        return cls._instance

    async def initialize(self):
        """初始化 MCP 客户端"""
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient
            from app.config import settings

            mcp_config = {}
            if settings.mcp_weather_url:
                mcp_config["weather"] = {
                    "url": settings.mcp_weather_url,
                    "transport": "streamable_http",
                }
            if settings.mcp_search_url:
                mcp_config["search"] = {
                    "url": settings.mcp_search_url,
                    "transport": "streamable_http",
                }

            if mcp_config:
                self.client = MultiServerMCPClient(mcp_config)
                app_logger.info("✅ MCP 客户端初始化完成")
            else:
                app_logger.warning("⚠️ 没有配置 MCP 服务，跳过初始化")

        except ImportError:
            app_logger.warning("⚠️ langchain-mcp-adapters 未安装，MCP 功能不可用")
        except Exception as e:
            app_logger.error(f"❌ MCP 客户端初始化失败: {e}")

    async def get_tools(self) -> list:
        """获取所有 MCP 工具"""
        if self.client is None:
            return []
        try:
            return await self.client.get_tools()
        except Exception as e:
            app_logger.error(f"❌ 获取 MCP 工具失败: {e}")
            return []


async def get_mcp_client() -> MCPClientManager:
    return await MCPClientManager.get_instance()
