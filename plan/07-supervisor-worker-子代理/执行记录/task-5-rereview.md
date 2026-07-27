# Task 5 Re-review

## Scope

Reviewed only the previous Task 5 findings in `task-5-review.md` and the fix diff `review-7c0dc1b..be98b86.diff`, after reading the required Task 5 brief/report/diff first.

## Verdicts

Spec verdict: Needs changes.

Quality verdict: Needs changes.

Approval decision: Not approved for Task 5 as-is because the prior Medium summary-grounding finding is not fully addressed.

## Previous finding status

### Important 1: Evidence ID remapping across repeated duplicate preference changes

Status: ADDRESSED.

The fix tracks every duplicate evidence ID for a dedupe key in `evidence_ids_by_key` and remaps all duplicate IDs to the final preferred evidence ID after each preference update (`app/governance/evidence.py:116`, `app/governance/evidence.py:154`, `app/governance/evidence.py:158`). This covers the prior `fallback ev-a` -> `search ev-b` -> `official ev-c` failure mode, and the added regression test exercises that exact shape.

### Important 2: Naive `valid_until` expiry comparison and governance failure isolation

Status: ADDRESSED.

The fix normalizes both `now` and each `valid_until` through `_as_aware_utc()` before comparison (`app/governance/evidence.py:117`, `app/governance/evidence.py:130`, `app/governance/evidence.py:179`). It also moved subagent coercion and governance conversion inside the worker `try` block (`app/agents/supervisor.py:421`), so governance/conversion exceptions are converted to failed `WorkerResult`s instead of failing the whole Supervisor graph.

### Medium 1: Downstream claim-like summary text is not fully governed

Status: NOT ADDRESSED.

The successful-response path is improved: completed worker summaries are now derived from reviewed claims/candidates/evidence metadata rather than raw `response.summary`. However, `_governed_summary()` still returns raw `response.summary` unchanged for any `SubagentResponse` with `status == "failed"` (`app/agents/supervisor.py:181`, `app/agents/supervisor.py:183`), and the synthesizer still includes every `result.summary` in the LLM evidence digest (`app/agents/supervisor.py:298`, `app/agents/supervisor.py:299`). A schema-valid failed subagent response can therefore still inject unsupported factual text into downstream synthesis. The fix report says only standard failure summaries are preserved, but the code preserves arbitrary failed-response summaries.

Minimal probe run during re-review:

```text
_subagent_response_to_worker_result(SubagentResponse(status="failed", summary="Unsupported factual summary should not reach synthesis.")).summary
=> Unsupported factual summary should not reach synthesis.
```

Recommended fix: for failed subagent responses, use a standard non-factual failure summary such as `"Domain subagent execution failed."` and place raw diagnostic detail only in warnings, or otherwise sanitize failure summaries before synthesis.

### Low 1: Planner docstring still described the old two-group dependency model

Status: ADDRESSED.

The planner docstring now states that the function generates one single parallel group of five independent research tasks (`app/agents/planner.py:7`), matching the current confirmed-destination contract.

## Required check areas

- Spec compliance: Needs changes due to incomplete summary grounding; other Task 5 merge/governance requirements checked here are satisfied by the fix diff.
- Failure isolation: ADDRESSED.
- Evidence ID remapping: ADDRESSED.
- Timezone handling: ADDRESSED for naive/aware expiry comparison.
- Summary grounding: NOT ADDRESSED for explicit failed subagent responses.
- Planner docstring: ADDRESSED.

## New issues introduced by this fix diff

No new Critical, Important, Medium, or Low issues found beyond the still-open prior Medium summary-grounding issue.

## Verification

Focused Task 5 suite run during re-review:

```text
.venv\Scripts\python.exe -m pytest tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py -q
20 passed, 2 warnings in 21.47s
```
