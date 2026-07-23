### Task 10: End-To-End Verification

**Files:**
- Test: `tests/test_main_agent_end_to_end.py`
- Modify only if a verified defect is found: files owned by Tasks 1 through 9.

**Interfaces:**
- Verifies the complete user-visible workflow without external network calls.

- [ ] **Step 1: Add end-to-end service tests**

Use in-memory/fake repositories and workers to verify these scenarios:

```text
new conversation -> proactive offer
"好的" -> form tool call
valid tool result -> Supervisor exactly once -> final response
"帮我规划成都旅行" -> prefilled form
"最近成都有什么好玩的" -> RAG only
recommend destination -> saved partial form -> city selected -> resumed form
refresh/history -> pending call restored
```

- [ ] **Step 2: Run the focused new suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_main_agent_contracts.py tests/test_tool_invocations.py tests/test_main_agent_routing.py tests/test_conversation_greeting.py tests/test_chat_main_agent_flow.py tests/test_trip_form_tool_flow.py tests/test_frontend_trip_form.py tests/test_main_agent_end_to_end.py -q`

Expected: PASS.

- [ ] **Step 3: Run the complete suite**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: all tests PASS; only known dependency/cache warnings may remain.

- [ ] **Step 4: Run syntax and diff checks**

Run: `.venv\Scripts\python.exe -m compileall -q app`

Expected: exit code 0.

Run: `git diff --check`

Expected: no whitespace errors in changed files.

- [ ] **Step 5: Start and visually verify the application**

Run the existing startup path, open the standalone HTML client, and verify desktop and mobile widths. Confirm no overlapping controls, all text fits, the form can be collapsed/restored, and an open question never flashes the planning form. Use browser screenshots for the proactive greeting, each form step, recommendation pause, and final itinerary.

- [ ] **Step 6: Review changes without committing**

Run: `git status --short` and `git diff --stat`.

Expected: only approved implementation, test, and documentation files plus the user's pre-existing changes.
