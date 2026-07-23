# Task 2 Report: Persist Tool Invocations

## Status

Complete. No commit or branch was created.

## Files

- Added `app/models/tool_invocation.py` with the `tool_invocation` SQLAlchemy table.
- Added `app/governance/tool_invocations.py` with record, protocol, in-memory, and PostgreSQL repositories.
- Updated `app/models/__init__.py` to register `ToolInvocation` with metadata.
- Added `tests/test_tool_invocations.py`.

## Red Evidence

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_tool_invocations.py -q
```

Output:

```text
=================================== ERRORS ====================================
_______________ ERROR collecting tests/test_tool_invocations.py _______________
ImportError while importing test module 'D:\Desktop\project\Trip\tests\test_tool_invocations.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
V:\PY\Python\Python313\Lib\importlib\__init__.py:88: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests\test_tool_invocations.py:3: in <module>
    from app.governance.tool_invocations import InMemoryToolInvocationRepository, ToolInvocationRecord
E   ModuleNotFoundError: No module named 'app.governance.tool_invocations'
============================== warnings summary ===============================
.venv\Lib\site-packages\_pytest\cacheprovider.py:475
  D:\Desktop\project\Trip\.venv\Lib\site-packages\_pytest\cacheprovider.py:475: PytestCacheWarning: could not create cache path D:\Desktop\project\Trip\.pytest_cache\v\cache\nodeids: [WinError 5] 拒绝访问。: 'D:\\Desktop\\project\\Trip\\.pytest_cache\\v\\cache'
    config.cache.set("cache/nodeids", sorted(self.cached_nodeids))

.venv\Lib\site-packages\_pytest\cacheprovider.py:429
  D:\Desktop\project\Trip\.venv\Lib\site-packages\_pytest\cacheprovider.py:429: PytestCacheWarning: could not create cache path D:\Desktop\project\Trip\.pytest_cache\v\cache\lastfailed: [WinError 5] 拒绝访问。: 'D:\\Desktop\\project\\Trip\\.pytest_cache\\v\\cache'
    config.cache.set("cache/lastfailed", self.lastfailed)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ===========================
ERROR tests/test_tool_invocations.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
2 warnings, 1 error in 0.41s
```

The failure is the expected missing Task 2 module.

## Green Evidence

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_tool_invocations.py tests/test_phase3_governance.py::test_governance_tables_are_registered_for_database_initialization -q
```

Output:

```text
.....                                                                    [100%]
============================== warnings summary ===============================
.venv\Lib\site-packages\langgraph\checkpoint\base\__init__.py:17
  D:\Desktop\project\Trip\.venv\Lib\site-packages\langgraph\checkpoint\base\__init__.py:17: LangChainPendingDeprecationWarning: The default value of `allowed_objects` will change in a future version. Pass an explicit value (e.g., allowed_objects='messages' or allowed_objects='core') to suppress this warning.
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

.venv\Lib\site-packages\jieba\_compat.py:18
  D:\Desktop\project\Trip\.venv\Lib\site-packages\jieba\_compat.py:18: UserWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html. The pkg_resources package is slated for removal as early as 2025-11-30. Refrain from using this package or pin to Setuptools<81.
    import pkg_resources

.venv\Lib\site-packages\_pytest\cacheprovider.py:475
  D:\Desktop\project\Trip\.venv\Lib\site-packages\_pytest\cacheprovider.py:475: PytestCacheWarning: could not create cache path D:\Desktop\project\Trip\.pytest_cache\v\cache\nodeids: [WinError 5] 拒绝访问。: 'D:\\Desktop\\project\\Trip\\.pytest_cache\\v\\cache'
    config.cache.set("cache/nodeids", sorted(self.cached_nodeids))

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
5 passed, 3 warnings in 9.48s
```

Diff command:

```powershell
git diff --check -- app/models app/governance/tool_invocations.py tests/test_tool_invocations.py
```

Output:

```text
warning: in the working copy of 'app/models/__init__.py', LF will be replaced by CRLF the next time Git touches it
```

The command exited with status `0`; the warning is from the already-modified model registry's configured line-ending conversion.

## Self-Review

- `ToolInvocation` has a unique call ID, UUID user and conversation foreign keys, JSON payload fields, nullable JSON result, version, and timestamps.
- All repository reads and mutations include `user_id`, including duplicate completion retrieval.
- In-memory records are deep copied and guarded by an async lock for deterministic concurrent behavior.
- PostgreSQL partial updates lock the owned row. Completion uses one conditional `UPDATE ... WHERE status != 'completed' RETURNING` statement; a duplicate reads and returns the stored row.
- The new model is imported by `app.models`, so database initialization includes `tool_invocation`.

## Concerns

- No live PostgreSQL integration test was run because this task's focused suite uses the in-memory repository and no database fixture is configured. The conditional update is implemented specifically for PostgreSQL concurrency.
- Pytest cannot write to the existing `.pytest_cache` directory, producing the recorded warning.
- The mandated diff check emits a CRLF conversion warning for pre-existing Task 1 changes in `app/models/__init__.py`; it does not report whitespace errors.

## Review Fix Evidence

### Changes

- Added `CompletionOutcome(record, completed_now)` and changed both `complete_once` implementations to return it. Only the caller whose state transition succeeded receives `completed_now=True`.
- Duplicate completion returns the persisted first result, including when the duplicate supplies a conflicting result.
- Added an ownership query for `Conversation.id` and `Conversation.user_id` before PostgreSQL insertion. A mismatched or missing conversation raises `PermissionError` before `session.add`.
- Retained the PostgreSQL conditional completion update. A row returned by `UPDATE ... RETURNING` maps to `completed_now=True`; the follow-up read maps to `completed_now=False`.
- Added coverage for transition claims, conflicting duplicate results, mutation isolation, PostgreSQL conversation ownership, and PostgreSQL update-result mapping.

### RED

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_tool_invocations.py -q
```

Output:

```text
FFF...FF                                                                 [100%]
FAILED tests/test_tool_invocations.py::test_tool_result_is_idempotent - AttributeError: 'ToolInvocationRecord' object has no attribute 'record'
FAILED tests/test_tool_invocations.py::test_duplicate_completion_keeps_the_first_result - AttributeError: 'ToolInvocationRecord' object has no attribute 'completed_now'
FAILED tests/test_tool_invocations.py::test_repository_records_are_isolated_from_caller_mutation - AttributeError: 'ToolInvocationRecord' object has no attribute 'record'
FAILED tests/test_tool_invocations.py::test_postgres_create_rejects_conversation_not_owned_by_user - Failed: DID NOT RAISE <class 'PermissionError'>
FAILED tests/test_tool_invocations.py::test_postgres_completion_marks_only_returned_update_row_as_newly_completed - AttributeError: 'ToolInvocationRecord' object has no attribute 'completed_now'
5 failed, 3 passed, 2 warnings in 0.70s
```

### GREEN

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_tool_invocations.py tests/test_phase3_governance.py::test_governance_tables_are_registered_for_database_initialization -q
```

Output:

```text
.........                                                                [100%]
============================== warnings summary ===============================
.venv\Lib\site-packages\langgraph\checkpoint\base\__init__.py:17
  D:\Desktop\project\Trip\.venv\Lib\site-packages\langgraph\checkpoint\base\__init__.py:17: LangChainPendingDeprecationWarning: The default value of `allowed_objects` will change in a future version. Pass an explicit value (e.g., allowed_objects='messages' or allowed_objects='core') to suppress this warning.
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

.venv\Lib\site-packages\jieba\_compat.py:18
  D:\Desktop\project\Trip\.venv\Lib\site-packages\jieba\_compat.py:18: UserWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html. The pkg_resources package is slated for removal as early as 2025-11-30. Refrain from using this package or pin to Setuptools<81.
    import pkg_resources

.venv\Lib\site-packages\_pytest\cacheprovider.py:475
  D:\Desktop\project\Trip\.venv\Lib\site-packages\_pytest\cacheprovider.py:475: PytestCacheWarning: could not create cache path D:\Desktop\project\Trip\.pytest_cache\v\cache\nodeids: [WinError 5] 拒绝访问。: 'D:\\Desktop\\project\\Trip\\.pytest_cache\\v\\cache'
    config.cache.set("cache/nodeids", sorted(self.cached_nodeids))

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
9 passed, 3 warnings in 6.71s
```

### PostgreSQL Limitation

No live PostgreSQL fixture is configured in this workspace, so the ownership and duplicate-completion branches use focused async fake sessions at the repository boundary. The tests exercise the actual repository control flow and statement-result mapping; a live concurrent PostgreSQL integration test remains outside the available test environment.

## PostgreSQL Concurrency Hardening

### Added Coverage

- Added `tests/test_tool_invocations_postgres.py`, marked `external` and skipped unless `RUN_POSTGRES_TESTS=1`.
- The opt-in test initializes the schema, creates a UUID-owned `User`, `Conversation`, and tool invocation, then concurrently submits two conflicting completion results.
- It requires exactly one `completed_now=True` outcome and verifies both callers receive the winning persisted result. Its `finally` block deletes the invocation, conversation, and user rows in one transaction.
- Added a unit assertion that compiles the actual PostgreSQL completion `UPDATE` and verifies its `call_id`, `user_id`, and `status != completed` predicates.

### Default Test Evidence

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_tool_invocations.py tests/test_tool_invocations_postgres.py tests/test_phase3_governance.py::test_governance_tables_are_registered_for_database_initialization -q
```

Output:

```text
........s.                                                               [100%]
============================== warnings summary ===============================
.venv\Lib\site-packages\langgraph\checkpoint\base\__init__.py:17
  D:\Desktop\project\Trip\.venv\Lib\site-packages\langgraph\checkpoint\base\__init__.py:17: LangChainPendingDeprecationWarning: The default value of `allowed_objects` will change in a future version. Pass an explicit value (e.g., allowed_objects='messages' or allowed_objects='core') to suppress this warning.
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

.venv\Lib\site-packages\jieba\_compat.py:18
  D:\Desktop\project\Trip\.venv\Lib\site-packages\jieba\_compat.py:18: UserWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html. The pkg_resources package is slated for removal as early as 2025-11-30. Refrain from using this package or pin to Setuptools<81.
    import pkg_resources

.venv\Lib\site-packages\_pytest\cacheprovider.py:475
  D:\Desktop\project\Trip\.venv\Lib\site-packages\_pytest\cacheprovider.py:475: PytestCacheWarning: could not create cache path D:\Desktop\project\Trip\.pytest_cache\v\cache\nodeids: [WinError 5] 拒绝访问。: 'D:\\Desktop\\project\\Trip\\.pytest_cache\\v\\cache'
    config.cache.set("cache/nodeids", sorted(self.cached_nodeids))

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
9 passed, 1 skipped, 3 warnings in 4.87s
```

The skipped test is `test_postgres_completion_is_atomic_for_conflicting_results`. It was not run because `RUN_POSTGRES_TESTS` is unset and this environment has no available Docker or reachable PostgreSQL service. Enable it in an integration environment with `RUN_POSTGRES_TESTS=1`.

## Final PostgreSQL Test Hardening

- Added a test-only `asyncio.Barrier(2)` session wrapper that holds both `ToolInvocation` completion `UPDATE` attempts until both concurrent callers have reached the contention point.
- The opt-in test now asserts the winning result is non-null, exactly equals one submitted payload, reloads the invocation from PostgreSQL after both calls finish, and verifies the reloaded result equals both returned records.
- Cleanup remains transactional in the test's `finally` block.

Command:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_tool_invocations.py tests/test_tool_invocations_postgres.py tests/test_phase3_governance.py::test_governance_tables_are_registered_for_database_initialization -q -rs
```

Output:

```text
........s.                                                               [100%]
============================== warnings summary ===============================
.venv\Lib\site-packages\langgraph\checkpoint\base\__init__.py:17
  D:\Desktop\project\Trip\.venv\Lib\site-packages\langgraph\checkpoint\base\__init__.py:17: LangChainPendingDeprecationWarning: The default value of `allowed_objects` will change in a future version. Pass an explicit value (e.g., allowed_objects='messages' or allowed_objects='core') to suppress this warning.
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

.venv\Lib\site-packages\jieba\_compat.py:18
  D:\Desktop\project\Trip\.venv\Lib\site-packages\jieba\_compat.py:18: UserWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html. The pkg_resources package is slated for removal as early as 2025-11-30. Refrain from using this package or pin to Setuptools<81.
    import pkg_resources

.venv\Lib\site-packages\_pytest\cacheprovider.py:475
  D:\Desktop\project\Trip\.venv\Lib\site-packages\_pytest\cacheprovider.py:475: PytestCacheWarning: could not create cache path D:\Desktop\project\Trip\.pytest_cache\v\cache\nodeids: [WinError 5] 拒绝访问。: 'D:\\Desktop\\project\\Trip\\.pytest_cache\\v\\cache'
    config.cache.set("cache/nodeids", sorted(self.cached_nodeids))

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ===========================
SKIPPED [1] tests\test_tool_invocations_postgres.py:57: requires RUN_POSTGRES_TESTS=1 and a reachable PostgreSQL database
9 passed, 1 skipped, 3 warnings in 5.99s
```
