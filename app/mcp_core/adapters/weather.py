"""高德天气 Source-native 适配器。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.mcp_core.reliability import ExternalServiceError, ResilientExecutor
from app.schemas.planning import Evidence


class AmapWeatherAdapter:
    def __init__(
        self,
        api_key: str | None = None,
        executor: ResilientExecutor | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key if api_key is not None else settings.amap_api_key
        self.executor = executor or ResilientExecutor(
            timeout_seconds=settings.external_timeout_seconds,
            max_retries=settings.external_max_retries,
        )
        self.client = client

    async def _request(self, url: str, params: dict) -> dict:
        if self.client is not None:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        async with httpx.AsyncClient(timeout=settings.external_timeout_seconds, trust_env=False) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def _resolve_adcode(self, city: str) -> str:
        async def operation():
            data = await self._request(
                "https://restapi.amap.com/v3/geocode/geo",
                {"key": self.api_key, "address": city, "output": "JSON"},
            )
            if data.get("status") != "1" or not data.get("geocodes"):
                raise ExternalServiceError(f"无法解析城市：{city}")
            return str(data["geocodes"][0]["adcode"])

        return await self.executor.execute(f"amap:adcode:{city}", operation, ttl_seconds=86400)

    async def query(self, city: str, forecast: bool = True) -> list[Evidence]:
        if not self.api_key:
            raise ExternalServiceError("未配置 AMAP_API_KEY")
        adcode = await self._resolve_adcode(city)
        extension = "all" if forecast else "base"

        async def operation():
            data = await self._request(
                "https://restapi.amap.com/v3/weather/weatherInfo",
                {"key": self.api_key, "city": adcode, "extensions": extension, "output": "JSON"},
            )
            if data.get("status") != "1":
                raise ExternalServiceError(f"高德天气查询失败：{data.get('info', '未知错误')}")
            return data

        data = await self.executor.execute(
            f"amap:weather:{adcode}:{extension}",
            operation,
            ttl_seconds=900 if forecast else 300,
        )
        now = datetime.now(timezone.utc)
        rows = data.get("forecasts", [{}])[0].get("casts", []) if forecast else data.get("lives", [])
        return [
            Evidence(
                content=(
                    f"{city} {row.get('date', row.get('reporttime', ''))}："
                    f"白天{row.get('dayweather', row.get('weather', '未知'))} "
                    f"{row.get('daytemp', row.get('temperature', '未知'))}℃；"
                    f"夜间{row.get('nightweather', '未知')} {row.get('nighttemp', '未知')}℃"
                ),
                source="高德开放平台天气服务",
                source_url="https://lbs.amap.com/api/webservice/guide/api/weatherinfo",
                retrieved_at=now,
                valid_from=now,
                valid_until=now + timedelta(hours=1),
                confidence=0.95,
                metadata={"provider": "amap", "city": city, "raw": row, "evidence_type": "weather"},
            )
            for row in rows
        ]
