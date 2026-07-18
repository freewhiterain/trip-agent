from datetime import datetime, timedelta, timezone

import pytest

from app.research.deep_research import DeepResearchService
from app.schemas.planning import Evidence


@pytest.mark.asyncio
async def test_deep_research_runs_multiple_queries_deduplicates_and_marks_conflicts():
    calls = []

    async def fake_search(query: str, max_results: int):
        calls.append(query)
        value = "开放" if "官方" in query else "关闭"
        return [
            Evidence(
                content=f"熊猫基地{value}",
                source=query,
                source_url=f"https://example.com/{value}",
                valid_until=datetime.now(timezone.utc) + timedelta(hours=1),
                metadata={"fact_key": "panda_base_status", "fact_value": value},
            )
        ]

    report = await DeepResearchService(fake_search).research("成都熊猫基地")

    assert len(calls) == 3
    assert len(report.evidence) == 2
    assert report.conflicts
    assert all(item.source_url for item in report.evidence)
