### Task 3 Report: Bounded Deep Search subgraph

Status: complete

Implemented:
- Added `app/research/deep_search.py` with `DeepSearchRequest`, `DeepSearchState`, typed `DeepSearchEvaluation`, and `run_deep_search(request) -> ResearchReport`.
- Enforced hard caps for rounds, tool calls, and per-search timeout with explicit warnings when capped or stopped.
- Added typed evaluator transitions using `needs_follow_up`, `missing_facts`, `conflicts`, `claims`, and `summary`; routing never depends on free-form text.
- Added deduplication by evidence id/source URL/content, freshness filtering, sanitized search/evaluator failure warnings, conflict propagation, and policy checks through `ToolPolicy`.
- Preserved `DeepResearchService.research()` compatibility and added `DeepResearchService.deep_search()` as an opt-in bounded path.
- Added focused tests for one-round completion, follow-up, hard max rounds, tool call limits, search failures, deduplication, freshness filtering, typed conflicts, and worker policy denial.

Verification:
- `.venv\Scripts\python.exe -m pytest tests/test_deep_search_subgraph.py -q`
  - Red before implementation: failed because `app.research.deep_search` was missing.
  - Green after implementation: `8 passed`.
- `.venv\Scripts\python.exe -m pytest tests/test_phase2_deep_research.py tests/test_subagent_tool_policy.py tests/test_subagent_contracts.py -q`
  - `12 passed`.

Concerns:
- `DeepSearchReport` subclasses the shared `ResearchReport` to expose Task 3 telemetry (`rounds`, `tool_calls`, `missing_facts`, `planned_queries`) without modifying the Task 1/2 schema file.
- Existing unrelated `.superpowers/sdd` deletions/modifications were present before this task and were not touched or staged.

Review fixes:
- Enforced the timeout budget across the entire search/evaluator loop, not only
  individual search calls.
- Dropped evaluator claims whose `evidence_ids` are empty or do not resolve to
  collected evidence, with an explicit warning.
- Added regression tests for whole-run timeout and unbound claim rejection.
