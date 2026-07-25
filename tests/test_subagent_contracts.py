from app.schemas.planning import Evidence
from app.schemas.research import Claim, EvidenceBoundCandidate, SubagentResponse


def test_subagent_response_requires_task_identity_and_structured_evidence():
    response = SubagentResponse(
        task_id="task-1",
        worker="attractions",
        status="completed",
        claims=[Claim(text="适合上午游览", evidence_ids=["ev-1"])],
        candidates=[EvidenceBoundCandidate(name="景点A", evidence_ids=["ev-1"])],
        evidence=[Evidence(id="ev-1", content="适合上午游览", source="official")],
    )
    assert response.task_id == "task-1"
    assert response.claims[0].evidence_ids == ["ev-1"]


def test_failure_response_has_no_unbound_claims():
    response = SubagentResponse(
        task_id="task-2", worker="weather", status="unavailable",
        warnings=["weather provider unavailable"],
    )
    assert response.claims == []
    assert response.candidates == []
