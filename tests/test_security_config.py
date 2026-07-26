from app.config import Settings
import pytest


class AsyncPool:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def test_database_and_redis_urls_escape_credentials():
    settings = Settings(
        _env_file=None,
        POSTGRES_USER="travel@user",
        POSTGRES_PASSWORD="p@ss:word/1",
        REDIS_PASSWORD="redis:@secret",
    )

    assert "travel%40user" in settings.database_url
    assert "p%40ss%3Aword%2F1" in settings.database_url
    assert "redis%3A%40secret" in settings.redis_url


def test_cors_defaults_to_local_frontend_allowlist():
    settings = Settings(_env_file=None)

    assert "*" not in settings.cors_origin_list
    assert "http://localhost:18000" in settings.cors_origin_list


@pytest.mark.asyncio
async def test_connection_manager_close_resets_singletons():
    from app.core.checkpointer import CheckpointerManager
    from app.core.store import StoreManager

    checkpointer = CheckpointerManager()
    checkpointer.pool = AsyncPool()
    checkpointer.checkpointer = object()
    store = StoreManager()
    store.pool = AsyncPool()
    store.store = object()

    await checkpointer.close()
    await store.close()

    assert checkpointer.pool is None
    assert checkpointer.checkpointer is None
    assert store.pool is None
    assert store.store is None
