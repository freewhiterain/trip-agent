"""
PostgreSQL Store 配置
长期记忆（用户级数据持久化）
"""
import asyncio
from typing import Optional, List
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from langgraph.store.postgres import AsyncPostgresStore
from psycopg_pool import AsyncConnectionPool

from app.config import settings
from app.utils.logger import app_logger
from app.core.memory_models import UserProfile, TravelHistory, TravelRecord, UserMemory


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


class UserMemoryService:
    """用户长期记忆服务"""

    def __init__(self, store: AsyncPostgresStore):
        self.store = store

    def _get_current_time(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    async def get_user_profile(self, user_id: str) -> UserProfile:
        try:
            result = await self.store.aget(
                namespace=("user_profiles", user_id),
                key="profile"
            )
            if result and result.value:
                return UserProfile(**result.value)
            return UserProfile()
        except Exception as e:
            app_logger.error(f"❌ 获取用户画像失败: {e}")
            return UserProfile()

    async def save_user_profile(self, user_id: str, profile: UserProfile):
        profile.updated_at = self._get_current_time()
        await self.store.aput(
            namespace=("user_profiles", user_id),
            key="profile",
            value=profile.model_dump()
        )
        app_logger.info(f"保存用户画像: {user_id}")

    async def update_travel_styles(self, user_id: str, styles: List[str]):
        profile = await self.get_user_profile(user_id)
        current_styles = set(profile.travel_styles)
        current_styles.update(styles)
        profile.travel_styles = list(current_styles)
        await self.save_user_profile(user_id, profile)

    async def update_dietary_restrictions(self, user_id: str, restrictions: List[str]):
        profile = await self.get_user_profile(user_id)
        current = set(profile.dietary_restrictions)
        current.update(restrictions)
        profile.dietary_restrictions = list(current)
        await self.save_user_profile(user_id, profile)

    async def update_food_preferences(self, user_id: str, preferences: List[str]):
        profile = await self.get_user_profile(user_id)
        current = set(profile.food_preferences)
        current.update(preferences)
        profile.food_preferences = list(current)
        await self.save_user_profile(user_id, profile)

    async def get_travel_history(self, user_id: str) -> TravelHistory:
        try:
            result = await self.store.aget(
                namespace=("travel_history", user_id),
                key="history"
            )
            if result and result.value:
                return TravelHistory(**result.value)
            return TravelHistory()
        except Exception as e:
            app_logger.error(f"❌ 获取出行历史失败: {e}")
            return TravelHistory()

    async def save_travel_history(self, user_id: str, history: TravelHistory):
        history.updated_at = self._get_current_time()
        await self.store.aput(
            namespace=("travel_history", user_id),
            key="history",
            value=history.model_dump()
        )
        app_logger.info(f"保存出行历史: {user_id}")

    async def add_completed_trip(
            self,
            user_id: str,
            destination: str,
            start_date: str,
            end_date: str,
            visited_attractions: List[str]
    ):
        history = await self.get_travel_history(user_id)
        trip = TravelRecord(
            destination=destination,
            start_date=start_date,
            end_date=end_date,
            visited_attractions=visited_attractions
        )
        history.completed_trips.append(trip)
        current_attractions = set(history.visited_attractions)
        current_attractions.update(visited_attractions)
        history.visited_attractions = list(current_attractions)
        await self.save_travel_history(user_id, history)
        app_logger.info(f"添加旅行记录: {user_id} -> {destination}")

    async def update_accommodation_preference(
            self,
            user_id: str,
            preferred_types: List[str] = None,
            avg_budget: float = None
    ):
        history = await self.get_travel_history(user_id)
        if preferred_types:
            current_types = set(history.accommodation_preference.preferred_types)
            current_types.update(preferred_types)
            history.accommodation_preference.preferred_types = list(current_types)
        if avg_budget:
            old_budget = history.accommodation_preference.avg_budget_per_night
            if old_budget:
                history.accommodation_preference.avg_budget_per_night = (old_budget + avg_budget) / 2
            else:
                history.accommodation_preference.avg_budget_per_night = avg_budget
        await self.save_travel_history(user_id, history)

    async def get_visited_destinations(self, user_id: str) -> List[str]:
        history = await self.get_travel_history(user_id)
        return list(set(trip.destination for trip in history.completed_trips))

    async def get_user_memory(self, user_id: str) -> UserMemory:
        profile, history = await asyncio.gather(
            self.get_user_profile(user_id),
            self.get_travel_history(user_id)
        )
        return UserMemory(user_id=user_id, profile=profile, history=history)

    async def format_memory_for_prompt(self, user_id: str) -> str:
        memory = await self.get_user_memory(user_id)
        parts = ["**用户历史偏好**："]

        if memory.profile.travel_styles:
            parts.append(f"- 旅行风格：{', '.join(memory.profile.travel_styles)}")
        if memory.profile.dietary_restrictions:
            parts.append(f"- 饮食禁忌：{', '.join(memory.profile.dietary_restrictions)}")
        if memory.profile.food_preferences:
            parts.append(f"- 饮食偏好：{', '.join(memory.profile.food_preferences)}")
        if memory.history.completed_trips:
            destinations = list(set(t.destination for t in memory.history.completed_trips))
            parts.append(f"- 去过的目的地：{', '.join(destinations[-5:])}")
        if memory.history.visited_attractions:
            parts.append(f"- 去过的景点：{', '.join(memory.history.visited_attractions[-10:])}（最近10个）")
        acc_pref = memory.history.accommodation_preference
        if acc_pref.preferred_types:
            parts.append(f"- 住宿偏好：{', '.join(acc_pref.preferred_types)}")
        if acc_pref.avg_budget_per_night:
            parts.append(f"- 住宿预算：约 {acc_pref.avg_budget_per_night:.0f} 元/晚")

        if len(parts) == 1:
            return ""
        return "\n".join(parts)


async def get_user_memory_service() -> UserMemoryService:
    store = await get_store()
    return UserMemoryService(store)
