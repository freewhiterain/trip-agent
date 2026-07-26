from datetime import datetime, timedelta, timezone

from app.governance.evidence import EvidenceGovernanceService
from app.schemas.planning import Evidence
from app.schemas.research import Claim, EvidenceBoundCandidate, ResearchConflict, ResearchReport, SubagentResponse


def _response(
    *,
    task_id: str = "task-a",
    worker: str = "attractions",
    claims=None,
    candidates=None,
    evidence=None,
    research_report=None,
    warnings=None,
) -> SubagentResponse:
    return SubagentResponse(
        task_id=task_id,
        worker=worker,
        status="completed",
        summary="ok",
        claims=claims or [],
        candidates=candidates or [],
        evidence=evidence or [],
        research_report=research_report,
        warnings=warnings or [],
    )


def test_governance_rejects_unbound_claims_and_expired_external_evidence():
    expired = Evidence(
        id="ev-expired",
        content="Museum hours changed last year.",
        source="official",
        source_url="https://example.test/museum",
        valid_until=datetime.now(timezone.utc) - timedelta(days=1),
    )
    reviewed = EvidenceGovernanceService().review(
        [
            _response(
                claims=[
                    Claim(text="Unbound current claim", evidence_ids=["missing"]),
                    Claim(text="Expired evidence claim", evidence_ids=["ev-expired"]),
                ],
                evidence=[expired],
            )
        ]
    )

    assert reviewed.claims == []
    assert reviewed.evidence == []
    assert reviewed.warnings


def test_governance_deduplicates_evidence_and_keeps_claim_bindings():
    first = Evidence(
        id="ev-a",
        content="Panda Base requires advance booking.",
        source="official",
        source_url="https://example.test/panda",
        confidence=0.9,
    )
    duplicate = Evidence(
        id="ev-b",
        content="Panda Base requires advance booking.",
        source="search",
        source_url="https://example.test/panda",
        confidence=0.6,
    )

    reviewed = EvidenceGovernanceService().review(
        [
            _response(
                claims=[Claim(text="Panda Base requires advance booking.", evidence_ids=["ev-b"])],
                candidates=[EvidenceBoundCandidate(name="Panda Base", evidence_ids=["ev-b"])],
                evidence=[first, duplicate],
            )
        ]
    )

    assert [item.id for item in reviewed.evidence] == ["ev-a"]
    assert reviewed.claims[0].evidence_ids == ["ev-a"]
    assert reviewed.candidates[0].evidence_ids == ["ev-a"]
    assert any("duplicate" in warning.lower() for warning in reviewed.warnings)


def test_governance_remaps_all_duplicate_ids_to_final_preferred_evidence():
    fallback = Evidence(
        id="ev-a",
        content="Museum requires advance booking.",
        source="fallback",
        source_url="https://example.test/museum",
    )
    search = Evidence(
        id="ev-b",
        content="Museum requires advance booking.",
        source="search",
        source_url="https://example.test/museum",
    )
    official = Evidence(
        id="ev-c",
        content="Museum requires advance booking.",
        source="official",
        source_url="https://example.test/museum",
    )

    reviewed = EvidenceGovernanceService().review(
        [
            _response(
                claims=[Claim(text="Museum requires advance booking.", evidence_ids=["ev-a"])],
                candidates=[EvidenceBoundCandidate(name="Museum", evidence_ids=["ev-a"])],
                evidence=[fallback, search, official],
            )
        ]
    )

    assert [item.id for item in reviewed.evidence] == ["ev-c"]
    assert reviewed.claims[0].evidence_ids == ["ev-c"]
    assert reviewed.candidates[0].evidence_ids == ["ev-c"]


def test_governance_normalizes_naive_valid_until_before_expiry_check():
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    expired_naive = Evidence(
        id="ev-naive-expired",
        content="Seasonal closure ended yesterday.",
        source="official",
        valid_until=datetime(2026, 8, 1, 11),
    )
    current_naive = Evidence(
        id="ev-naive-current",
        content="Museum is open this afternoon.",
        source="official",
        valid_until=datetime(2026, 8, 1, 13),
    )

    reviewed = EvidenceGovernanceService(now=now).review(
        [
            _response(
                claims=[
                    Claim(text="Expired naive claim", evidence_ids=["ev-naive-expired"]),
                    Claim(text="Current naive claim", evidence_ids=["ev-naive-current"]),
                ],
                evidence=[expired_naive, current_naive],
            )
        ]
    )

    assert [item.id for item in reviewed.evidence] == ["ev-naive-current"]
    assert [claim.text for claim in reviewed.claims] == ["Current naive claim"]


def test_governance_preserves_conflicts_and_warnings_without_resolving_them():
    conflict = ResearchConflict(
        fact_key="museum-hours",
        values=["open", "closed"],
        evidence_ids=["ev-open", "ev-closed"],
        description="Sources disagree about current opening status.",
    )
    report = ResearchReport(
        status="partial",
        conflicts=[conflict],
        warnings=["source disagreement needs human review"],
        evidence=[
            Evidence(id="ev-open", content="Museum is open.", source="official"),
            Evidence(id="ev-closed", content="Museum is closed.", source="search"),
        ],
    )

    reviewed = EvidenceGovernanceService().review([_response(research_report=report)])

    assert reviewed.conflicts == [conflict]
    assert "source disagreement needs human review" in reviewed.warnings
