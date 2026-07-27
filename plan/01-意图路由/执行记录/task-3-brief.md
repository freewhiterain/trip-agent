### Task 3: Implement Per-Turn Main-Agent Routing

**Files:**
- Create: `app/services/main_agent.py`
- Modify: `app/services/planning.py`
- Test: `tests/test_main_agent_routing.py`

**Interfaces:**
- Consumes: current user message and recent message context.
- Produces: `MainAgentService.decide(message: str, context: list[dict]) -> MainAgentDecision`.
- Reuses: `RequirementExtractor.extract()` only for safe form prefill, never for automatic Supervisor execution.

- [ ] **Step 1: Write routing tests**

```python
@pytest.mark.asyncio
async def test_affirmation_after_offer_opens_form():
    decision = await MainAgentService(use_llm=False).decide("好的", [{"role": "assistant", "content": "需要我帮你规划一下旅行吗？"}])
    assert decision.action == "collect_trip_requirements"


@pytest.mark.asyncio
async def test_direct_plan_request_opens_prefilled_form():
    decision = await MainAgentService(use_llm=False).decide("帮我规划一次成都旅行", [])
    assert decision.action == "collect_trip_requirements"
    assert decision.initial_values["destination"] == "成都"


@pytest.mark.asyncio
async def test_open_question_stays_rag_even_with_old_destination():
    context = [{"role": "tool", "content": '{"destination":"成都"}'}]
    decision = await MainAgentService(use_llm=False).decide("最近成都有什么好玩的？", context)
    assert decision.action == "answer_open_question"


@pytest.mark.asyncio
async def test_destination_recommendation_is_separate_action():
    decision = await MainAgentService(use_llm=False).decide("还没想好去哪，帮我推荐", [])
    assert decision.action == "recommend_destination"
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_main_agent_routing.py -q`

Expected: FAIL because `MainAgentService` does not exist.

- [ ] **Step 3: Implement deterministic routing plus structured LLM fallback**

Apply high-confidence rules first: explicit planning verbs, affirmation only after the proactive offer, recommendation requests, and open-question markers. For ambiguous turns with a configured key, call `get_llm().with_structured_output(MainAgentDecision)` using an instruction that forbids using historical slots as intent. Without a key, return `direct_response` rather than guessing planning intent.

- [ ] **Step 4: Remove automatic defaults from requirement conversion**

Make `TravelRequirementDraft.to_requirement()` the only conversion used by chat planning. Delete `to_requirement_with_defaults`, `DEFAULT_DAYS`, and `DEFAULT_DEPARTURE_OFFSET_DAYS`; preserve extraction solely for form prefill.

- [ ] **Step 5: Run routing and requirement tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_main_agent_routing.py tests/test_phase1_planning_contracts.py -q`

Expected: PASS after replacing tests that asserted automatic defaults.

