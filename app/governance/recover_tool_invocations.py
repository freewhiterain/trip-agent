from __future__ import annotations

import asyncio

from app.governance.tool_invocations import (
    DEFAULT_PROCESSING_LEASE_TIMEOUT,
    PostgresToolInvocationRepository,
    ToolInvocationRepository,
)


async def recover_stale_tool_invocations(
    repository: ToolInvocationRepository | None = None,
) -> int:
    """Recover stale claims during an exclusive maintenance window."""
    selected_repository = repository or PostgresToolInvocationRepository()
    return await selected_repository.release_stale_processing(
        DEFAULT_PROCESSING_LEASE_TIMEOUT
    )


async def main() -> None:
    recovered = await recover_stale_tool_invocations()
    print(f"Recovered {recovered} stale tool invocations")


if __name__ == "__main__":
    asyncio.run(main())
