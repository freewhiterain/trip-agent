### Task 5: Route Chat Through Main Agent And Emit Tool Calls

**Files:**
- Modify: `app/api/v1/chat.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/test_chat_main_agent_flow.py`

**Interfaces:**
- Consumes: `MainAgentService`, `PostgresToolInvocationRepository`, `answer_open_question`.
- Produces: chat SSE branches `tool_call`, RAG `token`/`done`, and direct `token`/`done`.

- [ ] **Step 1: Write endpoint-stream tests with fakes**

Cover four paths: affirmation creates one persisted form call; direct planning prepopulates destination; open question calls only RAG; direct response calls neither RAG nor Supervisor. Assert no branch reads `TripDraft` to decide intent.

- [ ] **Step 2: Verify old behavior fails tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_chat_main_agent_flow.py -q`

Expected: FAIL because chat still uses `hard_missing`, `classify_intent`, and `TripCoordinator.route`.

- [ ] **Step 3: Replace chat orchestration**

Save the user message, load bounded recent conversation messages, call `MainAgentService.decide`, and switch only on its explicit action. For `collect_trip_requirements`, persist a tool invocation and emit:

```python
yield sse(event("tool_call", ToolCallPayload(
    call_id=call_id,
    tool="collect_trip_requirements",
    arguments={"initial_values": decision.initial_values},
).model_dump()))
yield sse(event("done"))
```

Do not invoke Supervisor from the normal chat-message endpoint.

- [ ] **Step 4: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_chat_main_agent_flow.py tests/test_main_agent_routing.py -q`

Expected: PASS.

