# Task 5 Report: Route Chat Through Main Agent And Emit Tool Calls

## Status

Completed. The normal chat stream saves the user message, loads at most 12 prior messages, calls `MainAgentService.decide` once, and branches only on the returned action. It no longer reads trip drafts, classifies intent, or invokes a supervisor.

## Files

- Modified: `app/api/v1/chat.py`
- Added: `tests/test_chat_main_agent_flow.py`
- Added: `.superpowers/sdd/task-5-report.md`
- Not modified: `app/api/v1/__init__.py` is empty and route registration remains correctly owned by `app/main.py`; changing it would be a no-op.

## Red Evidence

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_chat_main_agent_flow.py -q
```

Output:

```text
FFFF                                                                     [100%]
4 failed, 4 warnings in 4.73s
```

Each failure was the expected missing integration: `AttributeError: ... chat has no attribute 'MainAgentService'`. This showed the existing chat endpoint had not yet been routed through the main agent.

## Green Evidence

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_chat_main_agent_flow.py tests\test_main_agent_routing.py -q
```

Output:

```text
20 passed, 2 warnings in 5.75s
```

Compatibility command:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_main_agent_contracts.py tests\test_conversation_greeting.py tests\test_phase4_api_and_sse.py -q
```

Output:

```text
14 passed, 2 skipped, 3 warnings in 5.06s
```

Diff check:

```powershell
git diff --check -- app/api/v1/chat.py tests/test_chat_main_agent_flow.py
```

Output: no whitespace errors. Git reported only its existing LF-to-CRLF conversion notice for `app/api/v1/chat.py`.

## Coverage

- An affirmation path creates exactly one persisted `collect_trip_requirements` call, stores assistant tool-call metadata, and emits `tool_call`, then `done`.
- An explicit planning path preserves prefilled destination values in both the persisted call and SSE payload.
- Open questions and destination recommendations use only RAG, emit `token` then `done`, and preserve their distinct action metadata.
- Direct replies use neither RAG nor the form repository and emit `token` then `done`.
- The stream source is checked to exclude `TripCoordinator`, draft repository/record reads, `hard_missing`, and `classify_intent`.

## Self-Review

- Conversation ownership and history endpoint behavior remain intact.
- Tool calls use UUID hex call IDs and the existing Postgres repository, which validates user/conversation ownership.
- The assistant message records action metadata; form calls also retain the complete tool-call payload for history restoration.
- Task 6's tool-result endpoint was not added or changed.

## Concerns

- PostgreSQL-backed tests remain skipped unless `RUN_POSTGRES_TESTS=1`; the focused stream tests use fakes while existing repository tests cover the repository separately.
- Pytest emitted existing dependency deprecation warnings and could not write `.pytest_cache` because of workspace permissions.

## Review Fix Evidence

### Red

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_chat_main_agent_flow.py tests\test_tool_invocations.py -q
```

Output:

```text
FF....F.......F.
4 failed, 12 passed, 4 warnings in 9.42s
```

The failures proved the reviewed gaps: the chat form branch called standalone `create`, `create_in_session` did not exist, and the context SQL lacked `message.id DESC` after `message.created_at DESC`.

### Green

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_chat_main_agent_flow.py tests\test_tool_invocations.py -q
```

Output:

```text
16 passed, 3 warnings in 4.48s
```

Requested focused command:

```powershell
.venv\Scripts\python.exe -m pytest tests\test_chat_main_agent_flow.py tests\test_main_agent_routing.py tests\test_tool_invocations.py tests\test_tool_invocations_postgres.py tests\test_main_agent_contracts.py tests\test_conversation_greeting.py tests\test_phase4_api_and_sse.py -q
```

Output:

```text
45 passed, 3 skipped, 3 warnings in 5.92s
```

### Review Scope

- `PostgresToolInvocationRepository.create_in_session` validates ownership and stages the invocation in a caller-owned transaction; standalone `create` retains its existing transaction behavior.
- The form branch stages the invocation and assistant metadata message in one `AsyncSession` transaction, then emits `tool_call` after the transaction exits successfully. The new failure test proves neither staged record persists when the assistant metadata write fails.
- Recent context excludes the saved current user message, limits to 12, orders by `created_at DESC, id DESC`, then reverses to chronological order before routing.
- Supervisor isolation is now behavioral: every chat branch monkeypatches `app.agents.supervisor.run_travel_planning` to fail if invoked and asserts no calls.
- Task 6's tool-result endpoint remains untouched.
