### Task 1: Define the result and evidence contracts

**Files:**
- Modify: `app/schemas/planning.py`
- Create: `app/schemas/research.py`
- Test: `tests/test_subagent_contracts.py`

**Interfaces:**
- Consumes: existing `TravelRequirement`, `Evidence`, `WorkerResult`, `CandidateOption`.
- Produces: `Claim`, `ResearchConflict`, `ResearchReport`, `SubagentResponse`, and evidence-aware candidate fields consumed by Subagent adapters and Supervisor reducers.

- [ ] **Step 1: Write failing contract tests**

```python
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
```

- [ ] **Step 2: Run the focused tests and verify collection fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_contracts.py -q`

Expected: FAIL during collection because the new contract types are not defined.

- [ ] **Step 3: Implement the minimal Pydantic contracts**

Define `EvidenceBoundCandidate`, `Claim`, `ResearchConflict`, `ResearchReport`, and `SubagentResponse` with explicit literals for statuses. Add optional evidence IDs to existing public models with empty-list defaults so old callers remain valid.

- [ ] **Step 4: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_contracts.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/planning.py app/schemas/research.py tests/test_subagent_contracts.py
git commit -m "feat: add subagent research result contracts"
```

