### Task 5: Add Evidence Governance and Supervisor result merging

**Files:**
- Create: `app/governance/evidence.py`
- Modify: `app/agents/planner.py`
- Modify: `app/agents/supervisor.py`
- Test: `tests/test_evidence_governance.py`
- Test: `tests/test_supervisor_subagent_merge.py`

**Interfaces:**
- Consumes: `SubagentResponse` keyed by `task_id`.
- Produces: `EvidenceGovernanceService.review(results) -> ReviewedResearch`, and a Supervisor state reducer `merge_worker_results(current, incoming)`.

- [ ] **Step 1: Write failing governance and merge tests**

```python
def test_governance_rejects_unbound_claims_and_expired_external_evidence():
    reviewed = EvidenceGovernanceService().review([response_with_invalid_claim()])
    assert reviewed.claims == []
    assert reviewed.warnings


def test_supervisor_merges_parallel_results_by_task_id():
    merged = merge_worker_results({}, {"task-a": response_a})
    merged = merge_worker_results(merged, {"task-b": response_b})
    assert set(merged) == {"task-a", "task-b"}
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py -q`

Expected: FAIL because the governance service and reducer are not defined.

- [ ] **Step 3: Implement deterministic evidence governance**

Validate evidence IDs, deduplicate by source URL/content, reject expired evidence, preserve unresolved conflicts, and rank source types without inventing a resolution. Return usable claims, usable candidates, conflicts, and warnings.

- [ ] **Step 4: Fix task dependencies and integrate the registry**

When `TravelRequirement.destination` is confirmed, create five independent tasks. Update Supervisor `Send` payloads to invoke the Subagent Registry and store results under `worker_results[task_id]`. Convert subagent failures to standard task results so other branches continue.

- [ ] **Step 5: Run focused Supervisor tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/governance/evidence.py app/agents/planner.py app/agents/supervisor.py tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py
git commit -m "feat: merge subagent results through evidence governance"
```

