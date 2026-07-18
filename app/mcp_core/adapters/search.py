"""Tavily 搜索 Source-native 适配器。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.mcp_core.reliability import ExternalServiceError, ResilientExecutor
from app.schemas.planning import Evidence


class TavilySearchAdapter:
    def __init__(self, api_key: str | None = None, executor: ResilientExecutor | None = None):
        self.api_key = api_key if api_key is not None else settings.tavily_api_key
        self.executor = executor or ResilientExecutor(
            timeout_seconds=settings.external_timeout_seconds,
            max_retries=settings.external_max_retries,
        )

    async def search(self, query: str, max_results: int = 5) -> list[Evidence]:
        if not self.api_key:
            raise ExternalServiceError("未配置 TAVILY_API_KEY")

        async def operation():
            async with httpx.AsyncClient(timeout=settings.external_timeout_seconds, trust_env=False) as client:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": self.api_key,
                        "query": query,
                        "max_results": max(1, min(max_results, 10)),
                        "search_depth": "advanced",
                    },
                )
                response.raise_for_status()
                return response.json()

        data = await self.executor.execute(
            f"tavily:{query}:{max_results}",
            operation,
            ttl_seconds=1800,
        )
        now = datetime.now(timezone.utc)
        return [
            Evidence(
                content=str(item.get("content") or item.get("title") or ""),
                source=str(item.get("title") or "Tavily 搜索结果"),
                source_url=item.get("url"),
                retrieved_at=now,
                valid_from=now,
                valid_until=now + timedelta(hours=6),
                confidence=float(item.get("score") or 0.6),
                metadata={"provider": "tavily", "query": query},
            )
            for item in data.get("results", [])
            if item.get("content") or item.get("title")
        ]
