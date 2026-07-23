### Task 4: Make New Conversations Proactive

**Files:**
- Modify: `app/api/v1/conversations.py`
- Test: `tests/test_conversation_greeting.py`

**Interfaces:**
- Produces: every newly created conversation has exactly one persisted assistant greeting.

- [ ] **Step 1: Write failing API/service test**

Assert that conversation creation persists `需要我帮你规划一下旅行吗？` as an assistant `Message` and returns it as `initial_message`; assert retrying a read does not create another greeting.

- [ ] **Step 2: Verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_conversation_greeting.py -q`

Expected: FAIL because no greeting is created.

- [ ] **Step 3: Persist the greeting in the same transaction**

After creating and flushing the conversation, create one assistant message with `extra_info={"kind": "conversation_offer"}`. Include the serialized message in the create response so the frontend can render it immediately.

- [ ] **Step 4: Run test**

Run: `.venv\Scripts\python.exe -m pytest tests/test_conversation_greeting.py tests/test_phase0_api_compatibility.py -q`

Expected: PASS.

