## Task 6 report - 2026-07-26

### Scope

- Added public typed subagent/research SSE event support while preserving legacy `token`, `result`, `error`, `done`, `tool_call`, and `tool_result` payload behavior.
- Reused the existing durable event service by wrapping the event repository with `PublishingEventRepository` and converting durable task events to public `SSEEvent` frames.
- Kept streamed research metadata public only: task ID, worker, tool name, round number, evidence count, conflict count, status, and warning codes.
- Did not modify Task 7 files or user-owned/deleted `.superpowers/sdd` files.

### TDD red

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_subagent_events_sse.py -q
```

Result: expected failure before implementation.

```text
FAILED tests\test_subagent_events_sse.py::test_subagent_events_keep_monotonic_sequence_and_legacy_fields
FAILED tests\test_subagent_events_sse.py::test_subagent_events_expose_only_public_typed_metadata
2 failed, 2 warnings
```

The failure showed the existing stream emitted only `result`, `token`, and `done`, with no subagent research events.

### Implementation

- `app/schemas/events.py`
  - Added typed public SSE event types for subagent start/completion, evidence collection, subagent tool calls, follow-up searches, and research conflicts.
- `app/governance/events.py`
  - Added `task_event_to_sse_event` mapping from durable task events to public SSE metadata.
  - Sanitized event payloads so hidden reasoning, raw evidence, raw conflicts, and warning text are not streamed.
- `app/api/v1/tools.py`
  - Used `PublishingEventRepository` to publish mapped research events into the active tool-result stream while preserving the legacy terminal `result`, `token`, and `done` frames.
  - Kept sequence ordering monotonic across research and legacy frames.
- `app/api/v1/chat.py`
  - Read and checked for compatibility; no code change was required because chat stream event construction and legacy fields remain unchanged.
- `tests/test_subagent_events_sse.py`
  - Added focused tests for monotonic ordering, legacy fields, and public-only typed metadata.

### Verification

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_subagent_events_sse.py -q
```

Result:

```text
2 passed, 2 warnings
```

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_subagent_events_sse.py tests/test_phase4_api_and_sse.py -q
```

Result:

```text
8 passed, 2 warnings
```

Additional compatibility command:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_trip_form_tool_flow.py tests/test_main_agent_contracts.py tests/test_main_agent_end_to_end.py -q
```

Result:

```text
31 passed, 2 warnings
```

### Concerns

- The verification commands still emit existing dependency warnings from `langgraph` and `jieba/pkg_resources`; no Task 6 failures were observed.
- The working tree already contained unrelated deleted `.superpowers/sdd` files and modified Task 7 files before Task 6 work started; these were intentionally left untouched.
