# Task 4 Report

## Status

Implemented proactive conversation creation. Each new conversation now persists exactly one assistant greeting in the same database session and transaction, and the create response exposes the serialized message as `initial_message`.

## Red Evidence

Command:

```text
.venv\Scripts\python.exe -m pytest tests/test_conversation_greeting.py -q
```

Result:

```text
1 failed, 1 warning in 1.85s
```

The failing assertion was `assert len(db.messages) == 1`; the existing API created zero messages.

## Green Evidence

Command:

```text
.venv\Scripts\python.exe -m pytest tests/test_conversation_greeting.py tests/test_phase0_api_compatibility.py -q
```

Result:

```text
3 passed, 2 warnings in 28.48s
```

The focused test verifies assistant role/content, `extra_info={"kind": "conversation_offer"}`, the `initial_message` response field, one commit, and no duplicate message after two reads.

## Concerns

- The focused test uses an in-memory session because the repository test suite does not provide a database fixture; live PostgreSQL integration was not exercised.
- The green run reports dependency warnings from LangGraph and `pkg_resources`, unrelated to this change.

## Additional Test Evidence

Added to `tests/test_conversation_greeting.py`:

- An actual FastAPI `TestClient` POST with auth and DB dependency overrides verifies JSON serialization of `initial_message`, including its role, content, conversation ID, and `extra_info.kind`, alongside standard conversation fields.
- The strict recording session verifies the conversation and greeting are added in that order before one commit snapshot containing both records.
- A commit failure returns HTTP 500, propagates through the endpoint, records one attempted commit, records zero successful commits, and triggers the override's rollback path.
- The HTTP GET read route leaves message count, add events, and commit attempts unchanged.

Command:

```text
.venv\Scripts\python.exe -m pytest tests/test_conversation_greeting.py tests/test_phase0_api_compatibility.py -q
```

Result:

```text
5 passed, 2 warnings in 13.29s
```

The tests use the strict fake session rather than a real SQLAlchemy async session because the repository does not provide a database test fixture and no live PostgreSQL integration was available for this focused test run.

## PostgreSQL Transaction Test Evidence

Added durable opt-in tests in `tests/test_conversation_greeting.py` guarded by `RUN_POSTGRES_TESTS=1`:

- A unique generated user exercises the real `async_session_maker`; successful creation is queried in a new session and must leave exactly one conversation and one assistant `conversation_offer` message.
- A SQLAlchemy `after_flush_postexec` hook raises after `create_conversation` has flushed both entities. The session rolls back, and a new session verifies that the staged conversation and its messages do not exist.
- Cleanup deletes messages before conversations before the unique user, with rollback and re-raise on cleanup failure.

Command:

```text
.venv\Scripts\python.exe -m pytest tests/test_conversation_greeting.py tests/test_phase0_api_compatibility.py -q
```

Result:

```text
...ss..                                                                  [100%]
5 passed, 2 skipped, 3 warnings in 9.54s
```

The two skipped tests are the PostgreSQL integration tests; this environment did not run them because `RUN_POSTGRES_TESTS=1` was not set. The warnings are the existing LangGraph, `pkg_resources`, and pytest cache-permission warnings.

## Final Finding Closure Evidence

Production transaction ownership now remains with `get_db`: `create_conversation` flushes and refreshes both entities for IDs and response serialization, but does not commit. Direct-call success tests explicitly commit their recording or PostgreSQL session after the handler returns. The HTTP override now yields, commits after the handler succeeds, and rolls back on exceptions; a read request records only that dependency-lifecycle commit and no new entities.

The PostgreSQL rollback test injects the forced exception from `after_flush_postexec` only after the conversation and greeting have both been flushed. It rolls back and verifies both IDs with a newly opened session. The success test commits in its first session and verifies exactly one conversation and one assistant `conversation_offer` message from a separate newly opened session.

Red command and result:

```text
.venv\Scripts\python.exe -m pytest tests/test_conversation_greeting.py tests/test_phase0_api_compatibility.py -q
1 failed, 4 passed, 2 skipped, 4 warnings in 7.88s
```

The red failure was the expected extra commit observed while the handler still owned the commit.

Final command and result:

```text
.venv\Scripts\python.exe -m pytest tests/test_conversation_greeting.py tests/test_phase0_api_compatibility.py -q
...ss..                                                                  [100%]
5 passed, 2 skipped, 3 warnings in 7.85s
```

The two skipped tests require `RUN_POSTGRES_TESTS=1` and a reachable PostgreSQL database. The warnings are the existing LangGraph deprecation, `pkg_resources`, and pytest cache-permission warnings.

## Final P1 Closure Evidence

The handler now flushes the conversation, stages and flushes the greeting, then explicitly commits both entities before refreshing and returning. The dependency override models `get_db` by issuing a later no-op commit after a successful handler; its commit snapshot is empty and does not represent duplicate data mutation. Handler commit failure returns HTTP 500, rolls back, and leaves zero persisted records in the harness.

The PostgreSQL rollback test installs a `before_commit` failure hook only after the handler has explicitly flushed both entities. The hook therefore exercises the handler commit boundary; the test rolls back and verifies the conversation and greeting IDs are absent from a separate session. PostgreSQL success relies on the handler commit and verifies the persisted rows from a separate session.

Red command and result:

```text
.venv\Scripts\python.exe -m pytest tests/test_conversation_greeting.py tests/test_phase0_api_compatibility.py -q
FFFss..                                                                  [100%]
3 failed, 2 passed, 2 skipped, 4 warnings in 7.76s
```

The red failures were the expected missing handler commit, missing dependency no-op commit assertion, and 200 response from the pre-fix teardown-only failure path.

Final command and result:

```text
.venv\Scripts\python.exe -m pytest tests/test_conversation_greeting.py tests/test_phase0_api_compatibility.py -q
...ss..                                                                  [100%]
5 passed, 2 skipped, 3 warnings in 8.20s
```

The two skipped tests are the guarded PostgreSQL integration tests because `RUN_POSTGRES_TESTS=1` was not set in this environment.
