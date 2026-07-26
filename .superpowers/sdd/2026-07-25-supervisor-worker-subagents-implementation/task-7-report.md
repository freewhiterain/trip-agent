# Task 7 Report: End-to-end supervisor subagent integration

## Summary

Implemented Task 7 directly on the current `main` checkout at the Task 6 base. The explicit feature mode `TRAVEL_AGENT_MODE=supervisor_subagents` is now accepted and routes through the existing Supervisor graph, which constructs the default `SubagentRegistry`. Existing `supervisor` mode compatibility is preserved, and unsupported modes continue to raise a startup configuration error.

Added end-to-end regression coverage for the new subagent path:

- confirmed trip planning runs the five domain subagents (`attractions`, `weather`, `transport`, `hotel`, `food`) in parallel;
- generated drafts retain worker/task/evidence traceability;
- no LLM/no provider availability returns a deterministic degraded draft with explicit warnings instead of failing startup;
- factory mode routing accepts `supervisor_subagents`, preserves `supervisor`, and rejects unsupported modes.

Updated the legacy Phase 2 mock-RAG endpoint test so it still verifies the old `WorkerRegistry` mock-RAG flow by injecting that registry explicitly. The assertion now tolerates public subagent progress SSE frames before the final result event while preserving duplicate-completion behavior.

## Files changed

- `app/agents/factory.py`
  - Accepts both `supervisor` and `supervisor_subagents`.
  - Keeps unsupported mode rejection.

- `app/config.py`
  - Adds the explicit supported travel-agent mode set.

- `tests/test_subagent_end_to_end.py`
  - New Task 7 end-to-end and factory coverage.

- `tests/test_phase2_mock_rag_e2e.py`
  - Keeps legacy mock-RAG WorkerRegistry coverage explicit and compatible with public research progress SSE frames.

## TDD record

1. Added `tests/test_subagent_end_to_end.py` before production changes.
2. Ran `.venv\Scripts\python.exe -m pytest tests/test_subagent_end_to_end.py -q`.
3. Verified red:
   - `supervisor_subagents` was rejected by `app/agents/factory.py`.
4. Implemented the minimal factory/config change.
5. Verified green on the focused Task 7 tests.

## Verification

- `.venv\Scripts\python.exe -m pytest tests/test_subagent_end_to_end.py -q`
  - Result: `6 passed, 1 warning in 5.43s`

- `.venv\Scripts\python.exe -m pytest tests/test_subagent_end_to_end.py tests/test_trip_form_tool_flow.py tests/test_main_agent_end_to_end.py -q`
  - Result: `32 passed, 2 warnings in 7.44s`

- `.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_rag_e2e.py::test_confirmed_form_runs_supervisor_once_across_five_category_scoped_workers -q`
  - Result: `1 passed, 2 warnings in 41.84s`
  - Note: slow because this legacy mock-RAG path initializes retrieval/vector components and attempts dense retrieval before falling back.

- Full suite was run before the later instruction to avoid full-suite execution:
  - `.venv\Scripts\python.exe -m pytest -q`
  - Result: `263 passed, 10 skipped, 2 warnings in 236.91s`

## Notes and concerns

- The focused Phase 2 mock-RAG endpoint test is slow (~42s) due to retrieval/vector initialization and failed dense retrieval fallback.
- Existing environment warnings remain:
  - `LangChainPendingDeprecationWarning` from LangGraph serializer defaults.
  - `pkg_resources` deprecation warning from `jieba`.
- No Task 6 event files were modified.
- Pre-existing user-owned `.superpowers/sdd` deletions/modifications in the working tree were left untouched.
