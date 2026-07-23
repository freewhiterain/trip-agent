from datetime import timedelta

import pytest

from app.governance.recover_tool_invocations import recover_stale_tool_invocations


@pytest.mark.asyncio
async def test_admin_recovery_delegates_to_repository():
    class Repository:
        async def release_stale_processing(self, lease_timeout):
            assert lease_timeout == timedelta(minutes=2)
            return 3

    assert await recover_stale_tool_invocations(Repository()) == 3
