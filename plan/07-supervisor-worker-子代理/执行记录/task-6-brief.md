### Task 6: Add research events and SSE compatibility

**Files:**
- Modify: `app/governance/events.py`
- Modify: `app/schemas/events.py`
- Modify: `app/api/v1/tools.py`
- Modify: `app/api/v1/chat.py`
- Test: `tests/test_subagent_events_sse.py`

**Interfaces:**
- Consumes: Supervisor and Subagent lifecycle callbacks.
- Produces: public events for subagent start/completion, tool calls, evidence collection, follow-up searches, and conflicts, while preserving legacy `token`, `result`, `error`, and `done` fields.

- [ ] **Step 1: Write failing event tests**

```python
@pytest.mark.asyncio
async def test_subagent_events_keep_monotonic_sequence_and_legacy_fields():
    events = await run_fake_planning_stream()
    assert [event.type for event in events] == [
        "subagent_started", "evidence_collected", "subagent_completed", "result", "token", "done"
    ]
    assert [event.sequence for event in events] == sorted(event.sequence for event in events)
    assert events[-1].legacy_payload()["type"] == "done"
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_events_sse.py -q`

Expected: FAIL because the new events are not emitted.

- [ ] **Step 3: Implement event mapping without exposing hidden reasoning**

Emit only typed public metadata: task ID, worker, tool name, round number, evidence count, conflict count, status, and warning codes. Keep user-facing SSE compatibility unchanged.

- [ ] **Step 4: Run focused SSE tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_events_sse.py tests/test_phase4_api_and_sse.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/governance/events.py app/schemas/events.py app/api/v1/tools.py app/api/v1/chat.py tests/test_subagent_events_sse.py
git commit -m "feat: stream subagent research events"
```

