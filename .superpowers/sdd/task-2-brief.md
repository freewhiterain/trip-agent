### Task 2: Persist Tool Invocations

**Files:**
- Create: `app/models/tool_invocation.py`
- Create: `app/governance/tool_invocations.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_tool_invocations.py`

**Interfaces:**
- Consumes: `ToolCallPayload`, `ToolResultRequest`.
- Produces: `ToolInvocationRecord`, `InMemoryToolInvocationRepository`, `PostgresToolInvocationRepository` with `create`, `get_for_user`, `update_partial`, and `complete_once`.

- [ ] **Step 1: Write repository behavior tests**

```python
@pytest.mark.asyncio
async def test_tool_result_is_idempotent():
    repository = InMemoryToolInvocationRepository()
    await repository.create(ToolInvocationRecord(call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements"))
    first = await repository.complete_once("c1", "u1", {"destination": "成都", "departure_date": "2026-08-10", "days": 4})
    second = await repository.complete_once("c1", "u1", first.result)
    assert first.status == "completed"
    assert second.version == first.version


@pytest.mark.asyncio
async def test_tool_call_is_user_scoped():
    repository = InMemoryToolInvocationRepository()
    await repository.create(ToolInvocationRecord(call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements"))
    assert await repository.get_for_user("c1", "u2") is None
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_tool_invocations.py -q`

Expected: FAIL because repository classes do not exist.

- [ ] **Step 3: Implement model and repositories**

Use a `tool_invocation` table with unique `call_id`, UUID user/conversation foreign keys, `tool`, `status`, JSON `arguments`, JSON `partial_values`, JSON nullable `result`, integer `version`, and timestamps. `complete_once` must lock or conditionally update only rows whose status is not `completed`; a duplicate completion returns the stored result without invoking downstream work again.

- [ ] **Step 4: Register the model and run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_tool_invocations.py tests/test_phase3_governance.py::test_governance_tables_are_registered_for_database_initialization -q`

Expected: PASS.

- [ ] **Step 5: Review diff without committing**

Run: `git diff --check -- app/models app/governance/tool_invocations.py tests/test_tool_invocations.py`

Expected: no output.

