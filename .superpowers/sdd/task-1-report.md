# Task 1 Implementation Report

## Status

DONE_WITH_CONCERNS

## Files Changed

- Created `app/schemas/tools.py` with the main-agent action, decision, trip form, tool-call, and tool-result contracts.
- Modified `app/schemas/events.py` to add `tool_call` and `tool_result` SSE types and expose their compatibility fields at the top level.
- Created `tests/test_main_agent_contracts.py` with contract coverage for required trip fields, explicit agent actions, and tool-call SSE payloads.
- Created this report at `.superpowers/sdd/task-1-report.md`.

## Tests Added

- `test_trip_form_requires_all_confirmed_fields`
- `test_main_agent_decision_has_explicit_action`
- `test_tool_call_event_exposes_payload`

## Test Commands And Outputs

### Red phase

Command:

```text
.venv\Scripts\python.exe -m pytest tests/test_main_agent_contracts.py -q
```

Output:

```text
ERROR collecting tests/test_main_agent_contracts.py
ModuleNotFoundError: No module named 'app.schemas.tools'
1 error during collection
```

### Focused contract tests

Command:

```text
.venv\Scripts\python.exe -m pytest tests/test_main_agent_contracts.py -q
```

Output: `3 passed, 1 warning in 0.06s`

### Contract and Phase 4 regression tests

Command:

```text
.venv\Scripts\python.exe -m pytest tests/test_main_agent_contracts.py tests/test_phase4_api_and_sse.py -q
```

Output: `9 passed, 3 warnings in 5.95s`

### Diff whitespace check

Command:

```text
git diff --check -- app/schemas/tools.py app/schemas/events.py tests/test_main_agent_contracts.py
```

Output: exit code `0`; no whitespace errors. Git emitted an existing line-ending normalization warning for `app/schemas/events.py`.

## Self-Review Notes

- `TripFormResult` requires destination, departure date, and days; days is constrained to 1 through 30.
- `MainAgentDecision.action` is restricted to the four specified routing actions.
- Tool names and result statuses use literals, while optional dictionaries use independent defaults.
- Existing token `content`, error `message`, ask fields, and nested `payload` behavior remain intact.
- Tool-call fields (`tool`, `call_id`, `arguments`) and tool-result fields (`tool`, `status`, `result`, `partial_values`) are copied to top-level legacy payloads.
- No commit or branch was created, and unrelated pre-existing worktree changes were left untouched.

## Concerns

- Pytest could not write `.pytest_cache` because of the workspace permission state; this produced a cache warning but did not affect test execution.
- The test run also reports pre-existing dependency warnings from LangGraph, `jieba`, and the cache provider. No functional failures were observed.

## Fix Review

- Added assertions for tool-call top-level `arguments`.
- Added tool-result coverage for top-level `tool`, `status`, `result`, and `partial_values` fields.
- Files changed: `tests/test_main_agent_contracts.py` and this report. No production files changed.

Command:

```text
.venv\Scripts\python.exe -m pytest tests/test_main_agent_contracts.py tests/test_phase4_api_and_sse.py -q
```

Output:

```text
..........                                                               [100%]
10 passed, 3 warnings in 4.85s
```

The warnings are the existing LangGraph deprecation, `jieba` package, and workspace pytest-cache permission warnings.

## Serialization Fix Review

- Root cause: `legacy_payload()` copied tool-result fields from raw `self.payload` after the outer event had been serialized with `mode="json"`.
- Added the expected ISO date assertion and a regression test that calls `json.dumps()` on a tool-result legacy payload.
- Updated `legacy_payload()` to copy tool-call and tool-result compatibility fields from the JSON-mode serialized payload.
- Files changed: `app/schemas/events.py`, `tests/test_main_agent_contracts.py`, and this report.

Failing reproduction command:

```text
.venv\Scripts\python.exe -m pytest tests/test_main_agent_contracts.py -q
```

Failing output: `2 failed, 3 passed, 2 warnings in 0.28s`; the failures were the Python `date` assertion and `TypeError: Object of type date is not JSON serializable`.

Final verification command:

```text
.venv\Scripts\python.exe -m pytest tests/test_main_agent_contracts.py tests/test_phase4_api_and_sse.py -q
```

Final output:

```text
...........                                                              [100%]
11 passed, 3 warnings in 5.34s
```

No commits were created.
