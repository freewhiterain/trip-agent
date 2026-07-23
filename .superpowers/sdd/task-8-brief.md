### Task 8: Build And Restore The Three-Step Frontend Tool

**Files:**
- Modify: `1_zhixing.html`
- Test: `tests/test_frontend_trip_form.py`

**Interfaces:**
- Consumes: SSE `tool_call`; submits `ToolResultRequest` to `/api/v1/chat/tools/{call_id}/result`.
- Produces: persistent UI state keyed by `call_id` with `currentStep`, `values`, `errors`, `collapsed`, and `submitting`.

- [ ] **Step 1: Write static behavior tests**

Assert the HTML contains handlers for `tool_call`, history `extra_info`, `submitTripToolResult`, `restorePendingTool`, all three field names, date minimum, days range, and recommendation status. Assert the old `renderAskCard` path is absent.

- [ ] **Step 2: Verify tests fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_frontend_trip_form.py -q`

Expected: FAIL because only shortcut chips exist.

- [ ] **Step 3: Add stable form layout and controls**

Create one un-nested tool panel below the relevant assistant/tool message. Use a `1/3` progress label, full-row destination options plus custom text, native date input, 2/3/5/7 day options plus numeric input, back/next controls, collapse and close icon buttons, validation messages, and a submit lock. Keep the normal chat input usable.

- [ ] **Step 4: Implement SSE and submit handling**

On `tool_call`, render or update by `call_id`. Submit recommendation as `status="recommend_destination"` with partial values. Submit the final result as `status="completed"`; parse returned SSE with the existing buffered frame parser and render final tokens. Never call `sendMessage()` to submit form values.

- [ ] **Step 5: Restore from history**

Change history rendering to use `renderContent` for assistant messages and inspect `extra_info.tool_call` / `extra_info.tool_result`. Restore the latest non-completed invocation and its saved partial values after refresh or conversation switch.

- [ ] **Step 6: Run frontend tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_frontend_trip_form.py tests/test_phase4_api_and_sse.py::test_frontend_buffers_partial_sse_frames -q`

Expected: PASS.

