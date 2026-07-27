### Task 3: Implement the bounded Deep Research subgraph

**Files:**
- Create: `app/research/deep_search.py`
- Modify: `app/research/deep_research.py`
- Test: `tests/test_deep_search_subgraph.py`

**Interfaces:**
- Consumes: `ResearchTask`, search function, `Evidence`, `ToolPolicy`.
- Produces: `DeepSearchRequest`, `DeepSearchState`, `ResearchReport`, and `run_deep_search(request) -> ResearchReport`.

- [ ] **Step 1: Write tests for one-round completion and follow-up**

```python
@pytest.mark.asyncio
async def test_deep_search_runs_follow_up_only_when_evidence_is_insufficient():
    calls = []

    async def search(query, limit):
        calls.append(query)
        return [Evidence(id=f"ev-{len(calls)}", content=query, source="web")]

    report = await run_deep_search(
        DeepSearchRequest(query="成都景点开放状态", worker="attractions", max_rounds=2),
        search=search,
        evaluator=FakeEvaluator(needs_follow_up=True),
    )
    assert report.rounds == 2
    assert len(calls) == 2
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_deep_search_subgraph.py -q`

Expected: FAIL because `run_deep_search` is not defined.

- [ ] **Step 3: Implement bounded search state and transitions**

Implement `plan_query -> search -> normalize -> evaluate -> follow_up/finish`. The evaluator must return typed `needs_follow_up`, `conflicts`, and `missing_facts`; never use free-form text as a routing condition. Enforce maximum rounds, maximum tool calls, timeout, deduplication, freshness filtering, and explicit warnings.

- [ ] **Step 4: Run focused Deep Search tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_deep_search_subgraph.py -q`

Expected: PASS, including max-round, search failure, deduplication, and conflict tests.

- [ ] **Step 5: Commit**

```bash
git add app/research/deep_search.py app/research/deep_research.py tests/test_deep_search_subgraph.py
git commit -m "feat: add bounded deep search subgraph"
```

