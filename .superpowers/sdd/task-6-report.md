# Task 6 Report: Accept Tool Results And Invoke Supervisor

## Status

Implemented `POST /api/v1/chat/tools/{call_id}/result` as an authenticated SSE endpoint.
It verifies user-scoped invocation ownership, stored tool identity, and lifecycle state; validates
completed trip results; preserves pending state for destination recommendations; and invokes the
Supervisor only for the versioned `processing` claim winner. Processing claims are lease-based and
renewed while the Supervisor runs without adding migration columns. Normal user requests claim only
`pending` invocations; stale `processing` records are not reclaimed by competing requests. Crash
recovery is deferred to a future exclusive startup/admin reconciliation mechanism.

## TDD Evidence

### RED

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py -q
```

Observed output before registering the route:

```text
FAILED tests/test_trip_form_tool_flow.py::test_trip_form_result_route_is_registered
AssertionError: assert '/api/v1/chat/tools/{call_id}/result' in {...}
1 failed, 4 warnings
```

After adding the route scaffold and then writing the behavioral tests, the same command produced:

```text
FAILED test_tool_result_rejects_another_users_call: assert 200 == 404
FAILED test_recommendation_keeps_call_pending_and_returns_rag_answer: ['done'] != ['token', 'tool_result', 'done']
FAILED test_completed_result_invokes_supervisor_once_with_confirmed_fields: ['done'] != ['result', 'token', 'done']
FAILED test_duplicate_completed_result_uses_stored_completion_without_supervisor: 'done' != 'result'
4 failed, 4 passed, 4 warnings
```

These failures showed the placeholder endpoint was registered but did not implement ownership,
recommendation, completion, or duplicate semantics.

### GREEN

Focused endpoint command:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py -q
```

Observed output:

```text
11 passed, 4 warnings
```

Required regression command, rerun after the final compatibility cleanup:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py tests\test_tool_invocations.py -q
```

Observed output:

```text
20 passed, 3 warnings in 7.75s
```

Syntax verification command:

```powershell
.venv\Scripts\python.exe -m compileall -q app\api\v1\tools.py tests\test_trip_form_tool_flow.py
```

Observed output: exit code `0` with no output.

## Files

- Created `app/api/v1/tools.py`: SSE endpoint, recommendation path, atomic completion path,
  durable duplicate response, assistant-message persistence.
- Modified `app/main.py`: registers the tools router under `/api/v1`.
- Modified `app/api/v1/__init__.py`: exports the tools module.
- Created `tests/test_trip_form_tool_flow.py`: route, ownership, validation, stored-tool,
  pending-state, recommendation, success, and duplicate-completion coverage.

## Self-Review

- `TripFormResult` is the sole source of completion fields; there are no date or day defaults.
- The Supervisor is called only after `claim_processing` returns `claimed=True`; the claim version
  is required by `finish_processing` and `release_processing`.
- A pre-existing or racing duplicate streams the stored durable assistant result with the stable
  task reference `call_id` and never calls the Supervisor.
- Recommendation persists merged `partial_values`, emits `token`, `tool_result` with
  `awaiting_destination`, and leaves the invocation pending.
- Successful completion persists tool-result metadata and the final assistant result in an
  assistant history message. Supervisor task events use `TaskEventService(PostgresEventRepository())`.
- SSE messages have monotonically increasing per-stream sequence numbers and end in `done`;
  execution errors yield `error` followed by `done`.

## Concerns

- The verified suite is dependency-isolated. A PostgreSQL-backed end-to-end request was not run
  because the repository's external Postgres tests require `RUN_POSTGRES_TESTS=1` and a reachable
  database.
- The test run still reports pre-existing dependency deprecation warnings and cannot write
  `.pytest_cache` because of a Windows permission denial. Neither warning affects the passing
  endpoint assertions.
- No commits or branches were created. Existing unrelated worktree changes were left untouched.

## Follow-Up Findings And Evidence

The review findings were converted into RED tests before implementation. The first follow-up run
reported:

```text
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py tests\test_tool_invocations.py -q
8 failed, 18 passed, 4 warnings
```

The failures covered the permissive extra field, missing processing repository methods, non-durable
duplicate output, unsanitized failure metadata, and active-processing handling. A subsequent RED
test for recommendation races reported:

```text
.venv\Scripts\python.exe -m pytest tests\test_tool_invocations.py::test_partial_values_do_not_update_a_non_pending_call -q
1 failed, 2 warnings
```

Final focused state-machine evidence:

```text
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py tests\test_tool_invocations.py -q
28 passed, 3 warnings
```

Final Task 6, repository, Postgres-conditional, SSE, and schema contract suites:

```text
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py tests\test_tool_invocations.py tests\test_tool_invocations_postgres.py tests\test_main_agent_contracts.py tests\test_phase4_api_and_sse.py -q
40 passed, 1 skipped, 3 warnings in 8.36s
```

The skipped test is the existing PostgreSQL concurrency test, gated by `RUN_POSTGRES_TESTS=1` and
a reachable database. `compileall` and `git diff --check` completed without errors; the remaining
warnings are dependency deprecations and the pre-existing Windows pytest-cache permission warning.

## Final Lease-Race Evidence

The lease-race follow-up added `renew_processing` to both repositories. The endpoint runs a named
heartbeat task at one-third of the lease interval during Supervisor execution, stops and awaits it
before finish/release, fences lease loss by claim version, and releases claims on normal failure or
client cancellation. User-request claims only transition `pending` records; old `processing` records
remain non-claimable regardless of age.

RED verification for the new behavior:

```text
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py::test_heartbeat_keeps_long_supervisor_claim_owned tests\test_tool_invocations.py::test_processing_renewal_requires_matching_version_and_status -q
2 failed
```

The failures were the expected missing renewal API and duplicate Supervisor invocation after the
short lease expired.

Final heartbeat/repository verification:

```text
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py tests\test_tool_invocations.py -q
34 passed, 3 warnings
```

This includes long-Supervisor renewal fencing, lease-loss conflict response, success/failure task
cleanup, client-cancellation cleanup, version/status renewal checks, and Postgres renewal SQL
predicates.

Final full requested verification:

```text
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py tests\test_tool_invocations.py tests\test_tool_invocations_postgres.py tests\test_main_agent_contracts.py tests\test_phase4_api_and_sse.py -q
45 passed, 1 skipped, 3 warnings in 4.86s
```

## Final P1 Lease-Race Evidence

The P1 fix runs `run_travel_planning` as an explicit named asyncio task and races it against the
heartbeat task. When renewal reports lease loss first, the planning task is cancelled and awaited;
the original claim version is retained and passed to version-fenced release. This returns a still-
owned claim to `pending`, while a changed status/version safely no-ops. No later user request can
reclaim an old `processing` record; crash recovery remains deferred to exclusive startup/admin
reconciliation. Planning-first completion cancels and awaits the heartbeat before durable finish.
Client cancellation cancels and awaits both tasks before re-raising.

RED verification before the explicit planning-task race:

```text
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py::test_lease_loss_cancels_blocking_supervisor_before_second_claim_side_effect -q
FAILED ... TimeoutError
```

The blocking Supervisor remained active after lease loss, reproducing the P1 race.

Focused GREEN verification:

```text
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py::test_lease_loss_cancels_blocking_supervisor_before_second_claim_side_effect -q
1 passed, 3 warnings
```

The final full requested suite is:

```text
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py tests\test_tool_invocations.py tests\test_tool_invocations_postgres.py tests\test_main_agent_contracts.py tests\test_phase4_api_and_sse.py -q
46 passed, 1 skipped, 3 warnings in 7.68s
```

## Final Single-Supervisor Safety Evidence

The final correction removed automatic stale-processing reclaim from both repositories while retaining
the compatibility `lease_timeout` argument. A user request observing `processing` now emits the
non-terminal processing response and never starts another Supervisor, even when `updated_at` is old.
Renewal false results and renewal exceptions cancel and await planning, then attempt release with the
original claim version; the version/status CAS prevents releasing a claim no longer owned.

Focused verification:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py tests\test_tool_invocations.py -q
```

Observed output:

```text
36 passed, 3 warnings in 8.99s
```

Coverage includes stale processing non-reclaimability, renewal exception release, renewal false/status
mismatch no-op behavior, long-Supervisor heartbeat renewal, cancellation before competing side effects,
durable duplicate replay, normal failure retry, and heartbeat task cleanup.

Final requested suite:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_trip_form_tool_flow.py tests\test_tool_invocations.py tests\test_tool_invocations_postgres.py tests\test_main_agent_contracts.py tests\test_phase4_api_and_sse.py -q
```

Observed output:

```text
47 passed, 1 skipped, 3 warnings in 8.89s
```

The skipped test is the existing PostgreSQL concurrency test gated by `RUN_POSTGRES_TESTS=1` and a
reachable database. Warnings remain the dependency deprecation warnings and the pre-existing Windows
pytest-cache permission warning.

Post-rename rerun of the same command:

```text
47 passed, 1 skipped, 3 warnings in 16.84s
```
