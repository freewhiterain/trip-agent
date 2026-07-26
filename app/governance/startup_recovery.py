"""应用启动时的孤儿认领恢复。

与 app/governance/recover_tool_invocations.py（管理员手工维护窗口）不同，
这里是每次进程启动都跑一次的自动恢复：进程被 SIGKILL 或容器重启打断时，
请求路径上的 release_processing 不会执行，记录会永远停在 processing。

安全性来自底层实现：release_stale_processing 取的是排他 advisory 锁，
而在线请求持有同一锁 ID 的共享锁，因此有活跃 worker 时恢复会抛错退出，
不会误抢别人正在处理的认领。任何异常都不允许阻断启动。
"""

from __future__ import annotations

from app.governance.tool_invocations import (
    DEFAULT_PROCESSING_LEASE_TIMEOUT,
    ToolInvocationRepository,
)
from app.utils.logger import app_logger


async def recover_orphaned_tool_invocations(
    repository: ToolInvocationRepository | None = None,
) -> int:
    """归还超过租约期仍停留在 processing 的记录，返回恢复条数。

    永不抛异常：拿不到锁（另一实例正在恢复或有活跃请求）和数据库尚未就绪
    都只记日志并返回 0。
    """
    if repository is None:
        from app.governance.tool_invocations import PostgresToolInvocationRepository

        repository = PostgresToolInvocationRepository()

    try:
        recovered = await repository.release_stale_processing(DEFAULT_PROCESSING_LEASE_TIMEOUT)
    except Exception as exc:
        app_logger.warning(f"启动时孤儿工具调用恢复已跳过: {type(exc).__name__}: {exc}")
        return 0

    if recovered:
        app_logger.info(f"启动时恢复了 {recovered} 条滞留在 processing 的工具调用")
    return recovered
