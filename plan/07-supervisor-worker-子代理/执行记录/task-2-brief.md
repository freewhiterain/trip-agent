### Task 2: Add domain tool policies and read-only tool adapters

**Files:**
- Create: `app/agents/subagents/tool_policy.py`
- Create: `app/agents/subagents/tools.py`
- Modify: `app/mcp_core/client.py`
- Test: `tests/test_subagent_tool_policy.py`

**Interfaces:**
- Consumes: `TaskType`, `ResearchTask`, existing `LocalKnowledgeService`, MCP manager, and external adapters.
- Produces: `ToolPolicy.for_worker(worker)`, `build_subagent_tools(worker, policy)`, and a tool result normalizer that returns only `Evidence` or typed provider errors.

- [ ] **Step 1: Write failing policy tests**

```python
def test_weather_policy_excludes_rag_and_deep_research():
    policy = ToolPolicy.for_worker("weather")
    assert policy.allowed_tools == {"weather_mcp", "weather_fallback_api"}
    assert policy.allow_deep_research is False


def test_attractions_policy_allows_rag_search_and_deep_research():
    policy = ToolPolicy.for_worker("attractions")
    assert {"local_rag", "search_mcp"}.issubset(policy.allowed_tools)
    assert policy.allow_deep_research is True
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_tool_policy.py -q`

Expected: FAIL because `ToolPolicy` is not defined.

- [ ] **Step 3: Implement policies and tool normalization**

Use explicit policies:

```python
POLICIES = {
    "weather": {"weather_mcp", "weather_fallback_api"},
    "transport": {"transport_mcp", "search_mcp"},
    "attractions": {"local_rag", "search_mcp", "deep_research"},
    "hotel": {"local_rag", "hotel_mcp", "search_mcp", "deep_research"},
    "food": {"local_rag", "search_mcp", "deep_research"},
}
```

Do not expose arbitrary MCP tools to a Subagent. Normalize provider errors into typed warnings and never return raw credentials or hidden tool payloads.

- [ ] **Step 4: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_tool_policy.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/agents/subagents app/mcp_core/client.py tests/test_subagent_tool_policy.py
git commit -m "feat: add domain subagent tool policies"
```

