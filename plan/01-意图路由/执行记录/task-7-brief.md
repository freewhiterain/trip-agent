### Task 7: Rename Destination Research To Attractions

**Files:**
- Modify: `app/schemas/planning.py`
- Modify: `app/agents/planner.py`
- Modify: `app/agents/supervisor.py`
- Rename: `app/agents/workers/destination.py` to `app/agents/workers/attractions.py`
- Modify: `app/agents/workers/registry.py`
- Modify: `app/agents/workers/__init__.py`
- Test: `tests/test_phase1_planning_contracts.py`
- Test: `tests/test_phase1_supervisor.py`

**Interfaces:**
- Produces: `TaskType` includes `attractions` and excludes `destination`; `AttractionsWorker` returns `worker="attractions"`.

- [ ] **Step 1: Update tests to require five responsibilities**

Expected task order/grouping remains two groups, but the first task type is `attractions`. Assert the itinerary builder reads attraction candidates from the attractions result.

- [ ] **Step 2: Run tests and verify failures**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py -q`

Expected: FAIL on the old `destination` task type.

- [ ] **Step 3: Apply the responsibility rename**

Rename classes, imports, registry keys, planner task type, event payloads, and `_result_by_worker` lookup. Keep the current evidence source unchanged because Worker data-source design is deferred.

- [ ] **Step 4: Run Supervisor tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py tests/test_phase6_coordinator.py -q`

Expected: Supervisor tests PASS; coordinator tests are removed or updated in Task 9 because coordinator is no longer part of chat routing.

