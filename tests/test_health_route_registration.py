"""/health 路由必须真的挂上。

app/api/v1/health.py 一直完整实现着 /health 和 /health/detail，但
app/main.py 从未 include_router 它，两个端点在线上一律 404——而
docs/superpowers/specs/2026-07-24-layered-user-memory-design.md:25
明确把 /health/detail 记作"在用"的基础设施。
"""

import pytest


def test_health_routes_are_registered_on_the_app():
    """不启动 lifespan，只检查路由表，避免依赖数据库和 MCP。"""
    from app.main import app

    paths = {route.path for route in app.routes}

    assert "/api/v1/health" in paths
    assert "/api/v1/health/detail" in paths


@pytest.mark.asyncio
async def test_basic_health_check_responds_without_external_dependencies():
    """基础健康检查不得触碰 checkpointer/store，否则无法用于存活探针。"""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
