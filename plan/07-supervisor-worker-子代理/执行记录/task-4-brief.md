### Task 4: Implement domain Subagent runners

**Files:**
- Create: `app/agents/subagents/base.py`
- Create: `app/agents/subagents/attractions.py`
- Create: `app/agents/subagents/weather.py`
- Create: `app/agents/subagents/transport.py`
- Create: `app/agents/subagents/hotel.py`
- Create: `app/agents/subagents/food.py`
- Create: `app/agents/subagents/registry.py`
- Test: `tests/test_domain_subagents.py`

**Interfaces:**
- Consumes: `ResearchTask`, `TravelRequirement`, `ToolPolicy`, `run_deep_search`, tool adapters.
- Produces: `DomainSubagent.run(task, requirement) -> SubagentResponse` and `SubagentRegistry.run(task, requirement) -> SubagentResponse`.

- [ ] **Step 1: Write failing tests for structured output and fallback**

```python
@pytest.mark.asyncio
async def test_attractions_subagent_returns_evidence_bound_candidates():
    agent = AttractionsSubagent(rag=FakeRag(), search=FakeSearch())
    result = await agent.run(attractions_task(), requirement())
    assert result.worker == "attractions"
    assert result.candidates
    assert all(item.evidence_ids for item in result.candidates)


@pytest.mark.asyncio
async def test_weather_subagent_does_not_call_deep_search():
    agent = WeatherSubagent(weather_mcp=FakeWeatherMcp(), deep_search=FailIfCalled())
    result = await agent.run(weather_task(), requirement())
    assert result.status == "completed"
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_domain_subagents.py -q`

Expected: FAIL because the Subagent classes are not defined.

- [ ] **Step 3: Implement the shared Subagent runner**

The base runner owns prompt construction, allowed-tool lookup, typed model output validation, evidence grounding, and fallback. Each domain class only defines its prompt, query builder, and provider order. The public result must never contain unbound candidates or unsupported factual fields.

- [ ] **Step 4: Implement all five domain Subagents**

Use these provider orders:

```text
weather: weather_mcp -> weather_fallback_api
transport: transport_mcp -> search_mcp
attractions: local_rag -> search_mcp -> deep_search when needed
hotel: local_rag -> hotel_mcp/search_mcp -> deep_search when needed
food: local_rag -> search_mcp -> deep_search when needed
```

- [ ] **Step 5: Run focused Subagent tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_domain_subagents.py -q`

Expected: PASS, including provider failures and no-LLM fallback.

- [ ] **Step 6: Commit**

```bash
git add app/agents/subagents tests/test_domain_subagents.py
git commit -m "feat: add domain subagent workers"
```

