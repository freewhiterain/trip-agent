### Task 7: End-to-end integration and regression coverage

**Files:**
- Modify: `app/agents/factory.py`
- Modify: `app/config.py`
- Modify: `app/services/main_agent.py`
- Test: `tests/test_subagent_end_to_end.py`
- Test: existing phase and tool-flow test files as needed

**Interfaces:**
- Consumes: the completed Subagent Registry, Supervisor graph, event stream, and existing chat/tool API.
- Produces: a feature-flagged end-to-end path with deterministic fallback and complete traceability.

- [ ] **Step 1: Write failing end-to-end tests**

```python
@pytest.mark.asyncio
async def test_confirmed_trip_runs_five_subagents_in_parallel_and_generates_draft():
    result = await run_fake_trip_with_subagents()
    assert {item.worker for item in result.worker_results} == {
        "attractions", "weather", "transport", "hotel", "food"
    }
    assert result.itinerary
    assert result.warnings == []
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_end_to_end.py -q`

Expected: FAIL because the Supervisor is still wired to the old Worker Registry.

- [ ] **Step 3: Wire the feature flag and factory**

Use an explicit mode such as `TRAVEL_AGENT_MODE=supervisor_subagents`. In tests, inject fake Subagents and fake providers. In environments without an LLM, preserve the old deterministic path and mark the result as degraded rather than failing startup.

- [ ] **Step 4: Run focused end-to-end tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_end_to_end.py tests/test_trip_form_tool_flow.py tests/test_main_agent_end_to_end.py -q`

Expected: PASS.

- [ ] **Step 5: Run the full regression suite**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: PASS with only the repository's existing opt-in external tests skipped.

- [ ] **Step 6: Commit**

```bash
git add app/agents/factory.py app/config.py app/services/main_agent.py tests/test_subagent_end_to_end.py
git commit -m "feat: integrate supervisor subagent planning flow"
```

## Self-Review Checklist

- [ ] Every domain Subagent returns the same typed envelope.
- [ ] Supervisor merges results by `task_id`, not by nondeterministic list position.
- [ ] Weather and transport do not invoke RAG or Deep Search.
- [ ] Attractions, hotel, and food invoke Deep Search only when policy/evaluator requires it.
- [ ] Deep Search has hard round, call, timeout, and read-only limits.
- [ ] Evidence Governance runs before route generation.
- [ ] Existing SSE compatibility, idempotency, safety, and fallback tests remain covered.
