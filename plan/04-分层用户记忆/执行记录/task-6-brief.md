### Task 6: 清理死代码

**Files:**
- Delete: `app/core/memory_models.py`
- Modify: `app/core/store.py`
- Test: 无新增测试文件；用全量回归验证

**Interfaces:**
- Consumes: 无
- Produces: 无（纯删除，`StoreManager`/`get_store`/`store_lifespan` 的对外接口不变）

- [ ] **Step 1: 确认没有遗漏的引用**

Run: `python -c "import subprocess; print(subprocess.run(['git', 'grep', '-n', 'UserMemoryService\\|memory_models\\|get_user_memory_service'], capture_output=True, text=True).stdout)"`
Expected: 只输出 `app/core/store.py` 自身的定义行（`class UserMemoryService`、
`def get_user_memory_service`）和 `app/core/memory_models.py` 的内容——没有
其它文件引用它们。如果发现了别的引用，先停下来检查那个引用是否也是死代码，
不要直接删。

- [ ] **Step 2: 删除 `app/core/memory_models.py`**

```bash
git rm app/core/memory_models.py
```

- [ ] **Step 3: 重写 `app/core/store.py`，去掉 `UserMemoryService` 和相关 import**

把整个文件内容替换为：

```python
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
```

- [ ] **Step 4: 确认应用仍能正常导入和启动依赖**

Run: `python -c "from app.core.store import StoreManager, get_store, store_lifespan; from app.main import app; print('ok')"`
Expected: 输出 `ok`，无 `ImportError`/`ModuleNotFoundError`

- [ ] **Step 5: 跑全量回归测试**

Run: `python -m pytest -q`
Expected: 全部 PASS（除 `external`/`RUN_POSTGRES_TESTS`/`RUN_OLLAMA_TESTS` 等
opt-in 测试按现有约定被跳过之外），没有因为删除死代码导致的 import 报错。

- [ ] **Step 6: 提交**

```bash
git add -A app/core/store.py app/core/memory_models.py
git commit -m "chore(memory): remove superseded UserMemoryService and memory_models dead code"
```

---

## 完成后验收清单

- [ ] `POST /tasks` 创建规划任务时，用户已确认的偏好会填充当次请求里为空的字段，已填写的字段不受影响。
- [ ] 偏好写入是 append-only：同一 `key` 多次确认会保留全部历史记录，读取时取最新一条。
- [ ] `itinerary.save`/`itinerary.overwrite` 审批通过后会产生一条可查询的行程历史记录；行程历史写入失败不影响行程保存本身。
- [ ] `app/core/memory_models.py` 已删除，`app/core/store.py` 只保留 `StoreManager`/`get_store`/`store_lifespan`。
- [ ] `python -m pytest -q` 全绿（opt-in 测试按约定跳过）。
