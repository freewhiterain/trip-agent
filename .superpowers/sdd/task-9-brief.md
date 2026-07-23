### Task 9: Remove Obsolete Slot And Coordinator Chat Logic

**Files:**
- Delete: `app/services/intent.py`
- Remove from chat path: `app/agents/coordinator.py`
- Modify or delete: `tests/test_phase5_intent_and_ask.py`
- Modify or delete: `tests/test_phase5_generate_first.py`
- Modify or delete: `tests/test_phase6_coordinator.py`
- Modify: `README.md`

**Interfaces:**
- Removes: `classify_intent`, `hard_missing`, `to_requirement_with_defaults`, `DESTINATION_QUICK_OPTIONS`, chat `TripCoordinator.route`, and `ask` shortcut-card behavior.

- [ ] **Step 1: Search for obsolete production references**

Run: `rg -n "classify_intent|hard_missing|to_requirement_with_defaults|DESTINATION_QUICK_OPTIONS|TripCoordinator|renderAskCard|type.*ask" app 1_zhixing.html`

Expected: only code scheduled for removal or non-chat compatibility references.

- [ ] **Step 2: Remove old production paths**

Delete the old intent service and automatic default conversion. Remove coordinator use from chat; if no non-chat caller remains, delete coordinator and its tests. Remove the `ask` SSE type once no compatibility consumer remains.

- [ ] **Step 3: Replace obsolete tests**

Delete assertions that encode “destination alone is enough”, default date, default days, and keyword-based slice routing. Keep extractor tests only where extraction supports form prefill.

- [ ] **Step 4: Update README**

Document the main Agent routes, proactive greeting, Tool Call/Tool Result lifecycle, mandatory fields, Supervisor boundary, and deferred Worker data-source design. Remove claims that chat uses generate-first defaults.

- [ ] **Step 5: Verify obsolete symbols are gone**

Run: `rg -n "classify_intent|to_requirement_with_defaults|DEFAULT_DEPARTURE_OFFSET_DAYS|renderAskCard" app tests 1_zhixing.html`

Expected: no matches.

