"""
PostgreSQL Store 配置
长期记忆（用户级数据持久化）
"""
import asyncio
from typing import Optional
from contextlib import asynccontextmanager
from langgraph.store.postgres import AsyncPostgresStore
from psycopg_pool import AsyncConnectionPool

from app.config import settings
from app.utils.logger import app_logger


class StoreManager:
    """Store 管理器（单例模式）"""

    _instance: Optional['StoreManager'] = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.store: Optional[AsyncPostgresStore] = None
        self.pool: Optional[AsyncConnectionPool] = None

    @classmethod
    async def get_instance(cls) -> 'StoreManager':
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
                    await cls._instance.initialize()
        return cls._instance

    async def initialize(self):
        if self.store is not None:
            app_logger.warning("Store 已初始化，跳过")
            return

        try:
            app_logger.info("初始化 PostgreSQL Store...")

            self.pool = AsyncConnectionPool(
                conninfo=settings.database_url,
                min_size=2,
                max_size=20,
                timeout=30,
                kwargs={"autocommit": True}
            )
            await self.pool.open()

            self.store = AsyncPostgresStore(self.pool)

            app_logger.info("✅ Store 初始化完成")

        except Exception as e:
            app_logger.error(f"❌ Store 初始化失败: {e}")
            raise

    async def close(self):
        if self.pool:
            await self.pool.close()
        self.pool = None
        self.store = None
        type(self)._instance = None
        if self.pool:
            app_logger.info("Connection Pool 已关闭")

    def get_store(self) -> AsyncPostgresStore:
        if self.store is None:
            raise RuntimeError("Store 未初始化，请先调用 initialize()")
        return self.store


async def get_store() -> AsyncPostgresStore:
    manager = await StoreManager.get_instance()
    return manager.get_store()


@asynccontextmanager
async def store_lifespan():
    manager = await StoreManager.get_instance()
    try:
        yield manager.get_store()
    finally:
        await manager.close()
