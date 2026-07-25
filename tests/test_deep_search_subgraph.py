import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.research.deep_search import (
    DeepSearchEvaluation,
    DeepSearchRequest,
    run_deep_search,
)
from app.schemas.planning import Evidence
from app.schemas.research import Claim, ResearchConflict


class FakeEvaluator:
    def __init__(
        self,
        *,
        needs_follow_up: bool = False,
        follow_up_rounds: int = 1,
        missing_facts: list[str] | None = None,
        conflicts: list[ResearchConflict] | None = None,
    ):
        self.needs_follow_up = needs_follow_up
        self.follow_up_rounds = follow_up_rounds
        self.missing_facts = missing_facts or []
        self.conflicts = conflicts or []
        self.calls = 0

    async def evaluate(self, state):
        self.calls += 1
        return DeepSearchEvaluation(
            needs_follow_up=self.needs_follow_up and self.calls <= self.follow_up_rounds,
            missing_facts=self.missing_facts,
            conflicts=self.conflicts,
            claims=[Claim(text=f"evaluated round {self.calls}", evidence_ids=["ev-1"])],
            summary=f"summary round {self.calls}",
        )


@pytest.mark.asyncio
async def test_deep_search_runs_follow_up_only_when_evidence_is_insufficient():
    calls = []

    async def search(query, limit):
        calls.append(query)
        return [Evidence(id=f"ev-{len(calls)}", content=query, source="web")]

    report = await run_deep_search(
        DeepSearchRequest(query="成都景点开放状态", worker="attractions", max_rounds=2),
        search=search,
        evaluator=FakeEvaluator(needs_follow_up=True, missing_facts=["官方开放时间"]),
    )

    assert report.rounds == 2
    assert report.tool_calls == 2
    assert len(calls) == 2
    assert calls[1] != calls[0]


@pytest.mark.asyncio
async def test_deep_search_finishes_after_one_round_when_evidence_is_sufficient():
    calls = []

    async def search(query, limit):
        calls.append(query)
        return [Evidence(id="ev-1", content="official current status", source="official")]

    report = await run_deep_search(
        DeepSearchRequest(query="panda base hours", worker="attractions", max_rounds=3),
        search=search,
        evaluator=FakeEvaluator(needs_follow_up=False),
    )

    assert report.status == "completed"
    assert report.rounds == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_deep_search_enforces_hard_max_rounds_with_warning():
    calls = []

    async def search(query, limit):
        calls.append(query)
        return [Evidence(id=f"ev-{len(calls)}", content=query, source="web")]

    report = await run_deep_search(
        DeepSearchRequest(query="latest hotel policy", worker="hotel", max_rounds=99),
        search=search,
        evaluator=FakeEvaluator(
            needs_follow_up=True,
            follow_up_rounds=99,
            missing_facts=["cancellation policy"],
        ),
    )

    assert report.rounds == 3
    assert len(calls) == 3
    assert report.status == "partial"
    assert any("max rounds" in warning.lower() for warning in report.warnings)


@pytest.mark.asyncio
async def test_deep_search_enforces_tool_call_limit_before_follow_up():
    calls = []

    async def search(query, limit):
        calls.append(query)
        return [Evidence(id=f"ev-{len(calls)}", content=query, source="web")]

    report = await run_deep_search(
        DeepSearchRequest(
            query="restaurant booking rules",
            worker="food",
            max_rounds=3,
            max_tool_calls=1,
        ),
        search=search,
        evaluator=FakeEvaluator(needs_follow_up=True, missing_facts=["reservation source"]),
    )

    assert report.rounds == 1
    assert report.tool_calls == 1
    assert report.status == "partial"
    assert any("tool call" in warning.lower() for warning in report.warnings)


@pytest.mark.asyncio
async def test_deep_search_search_failure_returns_partial_report_with_warning():
    async def search(query, limit):
        raise RuntimeError("provider leaked secret")

    report = await run_deep_search(
        DeepSearchRequest(query="hotel availability", worker="hotel", max_rounds=2),
        search=search,
        evaluator=FakeEvaluator(needs_follow_up=False),
    )

    assert report.status == "partial"
    assert report.evidence == []
    assert any("search failed" in warning.lower() for warning in report.warnings)
    assert all("secret" not in warning for warning in report.warnings)


@pytest.mark.asyncio
async def test_deep_search_deduplicates_and_filters_stale_evidence():
    now = datetime.now(timezone.utc)

    async def search(query, limit):
        return [
            Evidence(
                id="ev-1",
                content="current",
                source="official",
                source_url="https://example.com/current",
                valid_until=now + timedelta(hours=1),
            ),
            Evidence(
                id="ev-1",
                content="current duplicate",
                source="mirror",
                source_url="https://example.com/duplicate",
                valid_until=now + timedelta(hours=1),
            ),
            Evidence(
                id="ev-old",
                content="stale",
                source="old",
                source_url="https://example.com/old",
                valid_until=now - timedelta(minutes=1),
            ),
        ]

    report = await run_deep_search(
        DeepSearchRequest(query="museum hours", worker="attractions"),
        search=search,
        evaluator=FakeEvaluator(needs_follow_up=False),
        now=now,
    )

    assert [item.id for item in report.evidence] == ["ev-1"]
    assert any("stale" in warning.lower() for warning in report.warnings)
    assert any("duplicate" in warning.lower() for warning in report.warnings)


@pytest.mark.asyncio
async def test_deep_search_carries_typed_conflicts_from_evaluator():
    conflict = ResearchConflict(
        fact_key="opening_status",
        values=["open", "closed"],
        evidence_ids=["ev-1", "ev-2"],
        description="sources disagree",
    )

    async def search(query, limit):
        return [Evidence(id="ev-1", content="open", source="official")]

    report = await run_deep_search(
        DeepSearchRequest(query="site status", worker="attractions"),
        search=search,
        evaluator=FakeEvaluator(conflicts=[conflict]),
    )

    assert report.conflicts == [conflict]
    assert any("conflict" in warning.lower() for warning in report.warnings)


@pytest.mark.asyncio
async def test_deep_search_respects_tool_policy_for_workers_without_deep_research():
    calls = []

    async def search(query, limit):
        calls.append(query)
        return [Evidence(id="ev-1", content=query, source="web")]

    report = await run_deep_search(
        DeepSearchRequest(query="weather", worker="weather"),
        search=search,
        evaluator=FakeEvaluator(),
    )

    assert report.status == "unavailable"
    assert calls == []
    assert any("not allowed" in warning.lower() for warning in report.warnings)


@pytest.mark.asyncio
async def test_deep_search_enforces_timeout_for_the_whole_run():
    async def search(query, limit):
        return [Evidence(id="ev-1", content="current", source="official")]

    class SlowEvaluator:
        async def evaluate(self, state):
            await asyncio.sleep(0.05)
            return DeepSearchEvaluation(needs_follow_up=True)

    report = await run_deep_search(
        DeepSearchRequest(query="slow research", worker="attractions", timeout_seconds=0.01),
        search=search,
        evaluator=SlowEvaluator(),
    )

    assert report.status == "partial"
    assert any("total timeout" in warning.lower() for warning in report.warnings)


@pytest.mark.asyncio
async def test_deep_search_discards_claims_without_matching_evidence_ids():
    async def search(query, limit):
        return [Evidence(id="ev-1", content="current", source="official")]

    class UnboundEvaluator:
        async def evaluate(self, state):
            return DeepSearchEvaluation(
                claims=[Claim(text="unsupported", evidence_ids=["missing-evidence"])],
            )

    report = await run_deep_search(
        DeepSearchRequest(query="claim grounding", worker="attractions"),
        search=search,
        evaluator=UnboundEvaluator(),
    )

    assert report.claims == []
    assert any("unbound claim" in warning.lower() for warning in report.warnings)
