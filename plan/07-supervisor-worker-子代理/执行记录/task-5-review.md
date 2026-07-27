# Task 5 Review

## Verdicts

Spec verdict: Needs changes.

Quality verdict: Needs changes.

Approval decision: Not approved for Task 5 as-is.

## Findings

### Important

1. `app/governance/evidence.py:145-150` - Evidence ID remapping breaks when duplicate source ranking changes the preferred evidence more than once. The reducer remaps only the duplicate currently being processed and the immediately previous `existing.id`; earlier duplicate IDs still point at an ID that may later be removed from `usable_evidence`. A claim/candidate bound to the first duplicate can therefore be dropped even though it refers to the same surviving source/content. This violates the evidence ID validation/remapping and deduplication/source-ranking requirement. Minimal reproduction: process duplicate URL evidence as `fallback ev-a`, then `search ev-b`, then `official ev-c`; `ev-a` remains mapped to `ev-b`, `ev-b` is not usable, and claims bound to `ev-a` are dropped while `ev-c` survives.

2. `app/governance/evidence.py:116-129` and `app/agents/supervisor.py:399-411` - Expiry filtering can raise instead of isolate failure. `now` is timezone-aware, but `Evidence.valid_until` accepts naive datetimes; comparing a naive `valid_until` to aware `now` raises `TypeError`. Because governance runs after the worker/subagent `try` block, this exception is not converted into a failed `WorkerResult`, so one malformed-but-schema-valid evidence timestamp can fail the Supervisor graph instead of preserving sibling branches. This affects expiry filtering and failure isolation.

### Medium

1. `app/agents/supervisor.py:165-176` and `app/agents/supervisor.py:280-284` - Downstream claim-like text is not fully governed. `_subagent_response_to_worker_result()` governs candidates and evidence, but it discards `reviewed.claims` because `WorkerResult` has no claims field and keeps `response.summary` unchanged. The synthesizer then feeds `result.summary` into the LLM evidence digest, so unsupported factual summary text can still influence downstream planning even when governance drops the underlying claims/candidates/evidence.

### Low

1. `app/agents/planner.py:6-9` - The planner docstring still describes the old two-group dependency model even though confirmed-destination planning now intentionally creates one independent five-task group. This is a documentation quality issue, not a behavior blocker.

## Passing/acceptable areas checked

- `merge_worker_results()` stores and replaces results by `task_id`.
- Confirmed-destination planning produces five independent tasks in one group.
- The default Supervisor path now uses `create_default_subagent_registry()`.
- Registered subagent exceptions are converted to failed task results in the normal path.
- Legacy `WorkerRegistry` instances still work through `_coerce_subagent_response()` for the focused compatibility paths checked.
- Unresolved conflicts are preserved rather than resolved.

## Verification run during review

- `.venv\Scripts\python.exe -m pytest tests/test_supervisor_subagent_merge.py -q` passed.
- `.venv\Scripts\python.exe -m pytest tests/test_phase1_supervisor.py -q` passed.
- `.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_rag_e2e.py::test_single_worker_exception_does_not_block_other_worker_results -q` passed.
- `.venv\Scripts\python.exe -m pytest tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py -q` passed.
