# Task 5 Report: Evidence Governance and Supervisor Result Merging

## Status

Implemented Task 5 in the main checkout.

## Scope

Created:

- `app/governance/evidence.py`
- `tests/test_evidence_governance.py`
- `tests/test_supervisor_subagent_merge.py`

Modified:

- `app/agents/planner.py`
- `app/agents/supervisor.py`
- `tests/test_phase1_planning_contracts.py`

## Behavior Implemented

- Added `EvidenceGovernanceService.review(results) -> ReviewedResearch`.
- Validates that claims and candidates only survive when bound to usable evidence IDs.
- Drops evidence without IDs in governed `SubagentResponse` payloads.
- Deduplicates evidence by `source_url` first and normalized content second.
- Uses deterministic source ranking to choose the preferred duplicate without inventing conflict resolution.
- Rejects evidence whose `valid_until` has expired.
- Preserves unresolved `ResearchConflict` objects and warning messages.
- Added `merge_worker_results(current, incoming)` as a deterministic Supervisor reducer keyed by `task_id`.
- Changed confirmed-destination planning to produce five independent domain tasks in a single parallel group.
- Integrated the Supervisor with `SubagentRegistry` by default.
- Converts each subagent response through evidence governance before creating the downstream `WorkerResult`.
- Converts subagent/worker exceptions into standard failed task results so sibling branches continue.
- Preserves legacy `WorkerRegistry` compatibility by normalizing legacy ID-less evidence into deterministic IDs during conversion and preserving `is_mock`.

## TDD Notes

Initial focused Task 5 red run failed for the expected missing interfaces:

```text
.venv\Scripts\python.exe -m pytest tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py -q
ModuleNotFoundError: No module named 'app.governance.evidence'
ImportError: cannot import name 'merge_worker_results' from 'app.agents.supervisor'
```

After implementation, focused Task 5 tests passed.

## Verification

Focused Task 5 tests:

```text
.venv\Scripts\python.exe -m pytest tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py -q
7 passed, 1 warning in 6.09s
```

Required Task 5 supervisor/planning set:

```text
.venv\Scripts\python.exe -m pytest tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py -q
15 passed, 2 warnings in 21.25s
```

Relevant subagent contract/domain tests:

```text
.venv\Scripts\python.exe -m pytest tests/test_domain_subagents.py tests/test_subagent_contracts.py -q
12 passed in 0.22s
```

Legacy WorkerRegistry compatibility spot check after supervisor normalization:

```text
.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_rag_e2e.py::test_missing_category_fixture_and_llm_failure_degrade_without_losing_other_workers tests/test_phase2_mock_rag_e2e.py::test_single_worker_exception_does_not_block_other_worker_results -q
2 passed, 2 warnings in 82.22s
```

Syntax/import check:

```text
.venv\Scripts\python.exe -m compileall -q app\governance app\agents
exit 0
```

## Concerns

- The worktree had pre-existing unrelated `.superpowers/sdd` deletions/modifications, including Task 7 files, before Task 5 began. They were not modified or staged for this task.
- The phase-2 compatibility spot check is slow in this environment because the legacy retrieval path attempts unavailable dense/vector/graph dependencies and degrades after connection failures.

## Fix implementation follow-up

Addressed all Task 5 review findings with focused TDD regressions:

- Added red tests for duplicate evidence ID remapping across repeated ranking changes, naive `valid_until` expiry comparisons, governed-only worker summaries, governance/conversion failure isolation, and the planner docstring contract.
- Updated evidence governance so every duplicate ID for a source/content key remaps to the final preferred evidence, and naive/aware `valid_until` values are normalized to UTC before filtering.
- Moved subagent coercion and governance conversion inside the Supervisor worker failure isolation path so malformed-but-schema-adjacent payloads become failed `WorkerResult`s without blocking sibling branches.
- Replaced raw subagent summary propagation with summaries derived from governed claims/candidates/evidence metadata, preserving only standard failure summaries for failed workers.
- Updated the planner docstring to describe the current single parallel group of five independent tasks.

Red verification:

```text
.venv\Scripts\python.exe -m pytest tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py -q
5 failed, 15 passed, 2 warnings
```

Green verification:

```text
.venv\Scripts\python.exe -m pytest tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py -q
20 passed, 2 warnings
```

Concerns:

- Pre-existing unrelated `.superpowers/sdd` deletions/modifications were present before this fix work and were not touched or staged.
- Test warnings are dependency deprecation warnings from `langgraph`/`jieba`, not Task 5 failures.

## Fix implementation follow-up round 2

Addressed the remaining Task 5 re-review finding:

- Added a red regression for a schema-valid failed `SubagentResponse` whose raw summary contains unsupported factual text.
- Updated failed worker summary governance so failed responses always use the fixed non-factual summary `"Domain subagent execution failed."`.
- Left diagnostic details in `warnings`, which remain the only diagnostic channel used by the converted `WorkerResult`.

Red verification:

```text
.venv\Scripts\python.exe -m pytest tests/test_supervisor_subagent_merge.py::test_failed_worker_result_summary_uses_fixed_non_factual_text -q
1 failed, 1 warning
```

Green verification:

```text
.venv\Scripts\python.exe -m pytest tests/test_supervisor_subagent_merge.py::test_failed_worker_result_summary_uses_fixed_non_factual_text -q
1 passed, 1 warning
```

Full focused Task 5 verification:

```text
.venv\Scripts\python.exe -m pytest tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py -q
21 passed, 2 warnings
```

Concerns:

- Pre-existing unrelated `.superpowers/sdd` deletions/modifications remain outside this fix scope.
- Test warnings are dependency deprecation warnings from `langgraph`/`jieba`, not Task 5 failures.
