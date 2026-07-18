import asyncio

import httpx
import pytest

from app.mcp_core.adapters.weather import AmapWeatherAdapter
from app.mcp_core.reliability import CircuitOpenError, ExternalServiceError, ResilientExecutor


@pytest.mark.asyncio
async def test_executor_retries_then_caches_success():
    executor = ResilientExecutor(timeout_seconds=1, max_retries=2)
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("temporary")
        return {"ok": True}

    assert await executor.execute("key", operation, ttl_seconds=60) == {"ok": True}
    assert await executor.execute("key", operation, ttl_seconds=60) == {"ok": True}
    assert calls == 3


@pytest.mark.asyncio
async def test_executor_deduplicates_concurrent_requests():
    executor = ResilientExecutor(timeout_seconds=1, max_retries=0)
    calls = 0

    async def operation():
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return "done"

    values = await asyncio.gather(
        executor.execute("same", operation, ttl_seconds=60),
        executor.execute("same", operation, ttl_seconds=60),
    )

    assert values == ["done", "done"]
    assert calls == 1


@pytest.mark.asyncio
async def test_executor_opens_circuit_after_threshold():
    executor = ResilientExecutor(max_retries=0, failure_threshold=1, reset_after_seconds=60)

    async def operation():
        raise RuntimeError("down")

    with pytest.raises(ExternalServiceError):
        await executor.execute("first", operation, ttl_seconds=1)
    with pytest.raises(CircuitOpenError):
        await executor.execute("second", operation, ttl_seconds=1)


@pytest.mark.asyncio
async def test_weather_adapter_returns_structured_timed_evidence():
    def handler(request: httpx.Request):
        if request.url.path.endswith("/geocode/geo"):
            return httpx.Response(200, json={"status": "1", "geocodes": [{"adcode": "510100"}]})
        return httpx.Response(
            200,
            json={
                "status": "1",
                "forecasts": [{
                    "casts": [{
                        "date": "2026-08-01",
                        "dayweather": "晴",
                        "daytemp": "30",
                        "nightweather": "多云",
                        "nighttemp": "22",
                    }]
                }],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = AmapWeatherAdapter(api_key="test", client=client)
        evidence = await adapter.query("成都")

    assert len(evidence) == 1
    assert evidence[0].source == "高德开放平台天气服务"
    assert evidence[0].valid_until > evidence[0].retrieved_at
    assert evidence[0].metadata["provider"] == "amap"
