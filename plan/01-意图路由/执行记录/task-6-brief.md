### Task 6: Accept Tool Results And Invoke Supervisor

**Files:**
- Create: `app/api/v1/tools.py`
- Modify: `app/main.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/test_trip_form_tool_flow.py`

**Interfaces:**
- Adds: `POST /api/v1/chat/tools/{call_id}/result` returning SSE.
- Consumes: `ToolResultRequest`, tool repository, `TripFormResult`, `run_travel_planning`.

- [ ] **Step 1: Write tool endpoint tests**

Test ownership, missing fields, invalid dates/days, duplicate completion, recommendation pause, and successful Supervisor invocation. A successful call must pass exactly the confirmed `destination`, `departure_date`, and `days`; no defaults are permitted.

- [ ] **Step 2: Verify tests fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_trip_form_tool_flow.py -q`

Expected: FAIL with route not found.

- [ ] **Step 3: Implement completion and SSE planning stream**

Validate ownership and tool name. For `recommend_destination`, save `partial_values`, call the recommendation RAG service, emit recommendation content plus a `tool_result` status of `awaiting_destination`, and leave invocation incomplete. For `completed`, atomically complete the call, create `TravelRequirement` from the validated result, invoke Supervisor once, persist task events and assistant output, then emit `result`, `token`, and `done`.

- [ ] **Step 4: Verify idempotency**

The second identical submission returns the stored result or task reference and must not call the fake Supervisor a second time.

- [ ] **Step 5: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_trip_form_tool_flow.py tests/test_tool_invocations.py -q`

Expected: PASS.

