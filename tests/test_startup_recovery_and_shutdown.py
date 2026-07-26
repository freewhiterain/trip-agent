"""进程崩溃留下的 processing 记录必须能自动恢复。

正常路径（异常、取消、租约丢失）都会调用 release_processing 归还认领，
但进程被 SIGKILL / 容器重启打断时不会：心跳停了，记录永远停在
processing，用户重试只会拿到 processing_stream，那次工具调用彻底卡死。

release_stale_processing 早就实现好了，却只有管理员脚本
app/governance/recover_tool_invocations.py 会调用。把它接到应用启动上，
让每次重启自动清理上一轮崩溃的残留。

恢复本身是租约安全的：它取排他 advisory 锁，而在线 worker 持有同一个
锁 ID 的共享锁，所以有活跃请求时恢复会直接失败而不会误抢。
"""

import pytest

from app.governance import startup_recovery


@pytest.mark.asyncio
async def test_startup_recovery_releases_stale_processing_records():
    calls = []

    class Repository:
        async def release_stale_processing(self, lease_timeout):
            calls.append(lease_timeout)
            return 2

    recovered = await startup_recovery.recover_orphaned_tool_invocations(Repository())

    assert recovered == 2
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_startup_recovery_never_blocks_startup_when_another_instance_holds_the_lock():
    """并发实例启动时排他锁会抢不到，这不能让应用起不来。"""

    class LockedRepository:
        async def release_stale_processing(self, lease_timeout):
            raise RuntimeError("Another tool invocation recovery is already running")

    recovered = await startup_recovery.recover_orphaned_tool_invocations(LockedRepository())

    assert recovered == 0


@pytest.mark.asyncio
async def test_startup_recovery_swallows_database_outage():
    """数据库还没就绪时也不能让启动崩掉。"""

    class BrokenRepository:
        async def release_stale_processing(self, lease_timeout):
            raise OSError("connection refused")

    assert await startup_recovery.recover_orphaned_tool_invocations(BrokenRepository()) == 0


def test_lifespan_wires_startup_recovery_and_llm_client_shutdown():
    """契约测试：真正接进 lifespan 了，而不是只写了个函数没人调。"""
    import inspect

    from app import main

    source = inspect.getsource(main.lifespan)

    assert "recover_orphaned_tool_invocations" in source
    assert "aclose_http_clients" in source
