"""
PostgreSQL Checkpointer 配置
短期记忆（会话级状态持久化）
"""
import asyncio
from typing import Optional
from contextlib import asynccontextmanager
from psycopg_pool import AsyncConnectionPool
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from app.config import settings
from app.utils.logger import app_logger


class CheckpointerManager:
    """Checkpointer 管理器（单例模式）"""

    _instance: Optional['CheckpointerManager'] = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.pool: Optional[AsyncConnectionPool] = None
        self.checkpointer: Optional[AsyncPostgresSaver] = None

    @classmethod
    async def get_instance(cls) -> 'CheckpointerManager':
        """获取单例实例（线程安全）"""
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    await cls._instance.initialize()
        return cls._instance

    async def initialize(self):
        """初始化连接池和 Checkpointer"""
        if self.checkpointer is not None:
            app_logger.warning("⚠️ Checkpointer 已初始化，跳过")
            return

        try:
            app_logger.info("初始化 PostgreSQL Checkpointer...")

            self.pool = AsyncConnectionPool(
                conninfo=settings.database_url,
                min_size=2,
                max_size=20,
                timeout=30,
            )
            await self.pool.open()

            self.checkpointer = AsyncPostgresSaver(self.pool)

            app_logger.info("✅ Checkpointer 初始化完成")

        except Exception as e:
            app_logger.error(f"❌ Checkpointer 初始化失败: {e}")
            raise

    async def close(self):
        """关闭连接池"""
        if self.pool:
            await self.pool.close()
            app_logger.info("Checkpointer 连接池已关闭")

    def get_checkpointer(self) -> AsyncPostgresSaver:
        if self.checkpointer is None:
            raise RuntimeError("Checkpointer 未初始化，请先调用 initialize()")
        return self.checkpointer


async def get_checkpointer() -> AsyncPostgresSaver:
    """获取全局 Checkpointer 实例"""
    manager = await CheckpointerManager.get_instance()
    return manager.get_checkpointer()


@asynccontextmanager
async def checkpointer_lifespan():
    """Checkpointer 生命周期管理器"""
    manager = await CheckpointerManager.get_instance()
    try:
        yield manager.get_checkpointer()
    finally:
        await manager.close()
