"""面向旅行专题的多查询、多来源研究流程。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, Field

from app.rag.evidence import find_conflicts, require_fresh_evidence
from app.schemas.planning import Evidence


SearchFunction = Callable[[str, int], Awaitable[list[Evidence]]]


class DeepResearchReport(BaseModel):
    query: str
    evidence: list[Evidence]
    conflicts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DeepResearchService:
    def __init__(self, search: SearchFunction):
        self.search = search

    @staticmethod
    def build_queries(query: str) -> list[str]:
        return [query, f"{query} 官方信息", f"{query} 最新开放状态"]

    async def research(self, query: str, max_results_per_query: int = 3) -> DeepResearchReport:
        outcomes = await asyncio.gather(
            *(self.search(item, max_results_per_query) for item in self.build_queries(query)),
            return_exceptions=True,
        )
        evidence = []
        warnings = []
        seen = set()
        for outcome in outcomes:
            if isinstance(outcome, Exception):
                warnings.append(f"部分搜索失败：{type(outcome).__name__}")
                continue
            for item in outcome:
                key = (item.source_url, item.content)
                if key not in seen:
                    evidence.append(item)
                    seen.add(key)
        fresh = require_fresh_evidence(evidence)
        if len(fresh) < len(evidence):
            warnings.append("已排除过期证据。")
        return DeepResearchReport(
            query=query,
            evidence=fresh,
            conflicts=find_conflicts(fresh),
            warnings=warnings,
        )
