# Supervisor-Worker + Subagent Workers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将旅行规划系统升级为由 LangGraph Supervisor 调度五个领域 Subagent，并通过统一的结构化结果、证据治理和事件流生成可解释行程草稿。

**Architecture:** Supervisor 保持确定性图工作流，使用任务 ID 将五个领域任务并行分发给独立 Subagent。Subagent 内部使用领域专属 Prompt 和只读工具，按需调用受限 Deep Research 子图，最终只返回结构化 `WorkerResult` 和 `Evidence`；Supervisor 合并结果后依次执行证据治理、路线、预算和草稿生成。

**Tech Stack:** Python 3.11+, LangGraph 1.0.5, Pydantic 2.12, FastAPI SSE, PostgreSQL/Redis, Chroma, BM25, Ollama embeddings, MCP.

## Global Constraints

- 保持 `TravelRequirement`、`Evidence`、`WorkerResult` 的现有字段兼容，新增字段必须有默认值。
- Subagent 只允许调用只读 RAG、MCP、天气、地图和搜索工具，不能调用下单、预订、支付、写库或外部消息工具。
- 每个 Deep Research 任务最多 3 轮、10 次外部工具调用，并受单任务超时和并发限制约束。
- 所有事实性候选项必须通过 `evidence_ids` 绑定证据；证据不足时只能返回 `partial` 或 `unavailable`。
- 没有 LLM、MCP 或向量库时必须退回现有确定性/RAG 摘要逻辑。
- 不把隐藏思考过程写入 API、数据库、日志或 SSE；只记录公开的阶段、工具和证据事件。
- 现有 `.superpowers/sdd` 下的用户修改不得加入本功能提交。

---

### Task 1: Define the result and evidence contracts

**Files:**
- Modify: `app/schemas/planning.py`
- Create: `app/schemas/research.py`
- Test: `tests/test_subagent_contracts.py`

**Interfaces:**
- Consumes: existing `TravelRequirement`, `Evidence`, `WorkerResult`, `CandidateOption`.
- Produces: `Claim`, `ResearchConflict`, `ResearchReport`, `SubagentResponse`, and evidence-aware candidate fields consumed by Subagent adapters and Supervisor reducers.

- [ ] **Step 1: Write failing contract tests**

```python
def test_subagent_response_requires_task_identity_and_structured_evidence():
    response = SubagentResponse(
        task_id="task-1",
        worker="attractions",
        status="completed",
        claims=[Claim(text="适合上午游览", evidence_ids=["ev-1"])],
        candidates=[EvidenceBoundCandidate(name="景点A", evidence_ids=["ev-1"])],
        evidence=[Evidence(id="ev-1", content="适合上午游览", source="official")],
    )
    assert response.task_id == "task-1"
    assert response.claims[0].evidence_ids == ["ev-1"]


def test_failure_response_has_no_unbound_claims():
    response = SubagentResponse(
        task_id="task-2", worker="weather", status="unavailable",
        warnings=["weather provider unavailable"],
    )
    assert response.claims == []
    assert response.candidates == []
```

- [ ] **Step 2: Run the focused tests and verify collection fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_contracts.py -q`

Expected: FAIL during collection because the new contract types are not defined.

- [ ] **Step 3: Implement the minimal Pydantic contracts**

Define `EvidenceBoundCandidate`, `Claim`, `ResearchConflict`, `ResearchReport`, and `SubagentResponse` with explicit literals for statuses. Add optional evidence IDs to existing public models with empty-list defaults so old callers remain valid.

- [ ] **Step 4: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_contracts.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/planning.py app/schemas/research.py tests/test_subagent_contracts.py
git commit -m "feat: add subagent research result contracts"
```

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

### Task 5: Add Evidence Governance and Supervisor result merging

**Files:**
- Create: `app/governance/evidence.py`
- Modify: `app/agents/planner.py`
- Modify: `app/agents/supervisor.py`
- Test: `tests/test_evidence_governance.py`
- Test: `tests/test_supervisor_subagent_merge.py`

**Interfaces:**
- Consumes: `SubagentResponse` keyed by `task_id`.
- Produces: `EvidenceGovernanceService.review(results) -> ReviewedResearch`, and a Supervisor state reducer `merge_worker_results(current, incoming)`.

- [ ] **Step 1: Write failing governance and merge tests**

```python
def test_governance_rejects_unbound_claims_and_expired_external_evidence():
    reviewed = EvidenceGovernanceService().review([response_with_invalid_claim()])
    assert reviewed.claims == []
    assert reviewed.warnings


def test_supervisor_merges_parallel_results_by_task_id():
    merged = merge_worker_results({}, {"task-a": response_a})
    merged = merge_worker_results(merged, {"task-b": response_b})
    assert set(merged) == {"task-a", "task-b"}
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py -q`

Expected: FAIL because the governance service and reducer are not defined.

- [ ] **Step 3: Implement deterministic evidence governance**

Validate evidence IDs, deduplicate by source URL/content, reject expired evidence, preserve unresolved conflicts, and rank source types without inventing a resolution. Return usable claims, usable candidates, conflicts, and warnings.

- [ ] **Step 4: Fix task dependencies and integrate the registry**

When `TravelRequirement.destination` is confirmed, create five independent tasks. Update Supervisor `Send` payloads to invoke the Subagent Registry and store results under `worker_results[task_id]`. Convert subagent failures to standard task results so other branches continue.

- [ ] **Step 5: Run focused Supervisor tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/governance/evidence.py app/agents/planner.py app/agents/supervisor.py tests/test_evidence_governance.py tests/test_supervisor_subagent_merge.py
git commit -m "feat: merge subagent results through evidence governance"
```

### Task 6: Add research events and SSE compatibility

**Files:**
- Modify: `app/governance/events.py`
- Modify: `app/schemas/events.py`
- Modify: `app/api/v1/tools.py`
- Modify: `app/api/v1/chat.py`
- Test: `tests/test_subagent_events_sse.py`

**Interfaces:**
- Consumes: Supervisor and Subagent lifecycle callbacks.
- Produces: public events for subagent start/completion, tool calls, evidence collection, follow-up searches, and conflicts, while preserving legacy `token`, `result`, `error`, and `done` fields.

- [ ] **Step 1: Write failing event tests**

```python
@pytest.mark.asyncio
async def test_subagent_events_keep_monotonic_sequence_and_legacy_fields():
    events = await run_fake_planning_stream()
    assert [event.type for event in events] == [
        "subagent_started", "evidence_collected", "subagent_completed", "result", "token", "done"
    ]
    assert [event.sequence for event in events] == sorted(event.sequence for event in events)
    assert events[-1].legacy_payload()["type"] == "done"
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_events_sse.py -q`

Expected: FAIL because the new events are not emitted.

- [ ] **Step 3: Implement event mapping without exposing hidden reasoning**

Emit only typed public metadata: task ID, worker, tool name, round number, evidence count, conflict count, status, and warning codes. Keep user-facing SSE compatibility unchanged.

- [ ] **Step 4: Run focused SSE tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_events_sse.py tests/test_phase4_api_and_sse.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/governance/events.py app/schemas/events.py app/api/v1/tools.py app/api/v1/chat.py tests/test_subagent_events_sse.py
git commit -m "feat: stream subagent research events"
```

### Task 7: End-to-end integration and regression coverage

**Files:**
- Modify: `app/agents/factory.py`
- Modify: `app/config.py`
- Modify: `app/services/main_agent.py`
- Test: `tests/test_subagent_end_to_end.py`
- Test: existing phase and tool-flow test files as needed

**Interfaces:**
- Consumes: the completed Subagent Registry, Supervisor graph, event stream, and existing chat/tool API.
- Produces: a feature-flagged end-to-end path with deterministic fallback and complete traceability.

- [ ] **Step 1: Write failing end-to-end tests**

```python
@pytest.mark.asyncio
async def test_confirmed_trip_runs_five_subagents_in_parallel_and_generates_draft():
    result = await run_fake_trip_with_subagents()
    assert {item.worker for item in result.worker_results} == {
        "attractions", "weather", "transport", "hotel", "food"
    }
    assert result.itinerary
    assert result.warnings == []
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_end_to_end.py -q`

Expected: FAIL because the Supervisor is still wired to the old Worker Registry.

- [ ] **Step 3: Wire the feature flag and factory**

Use an explicit mode such as `TRAVEL_AGENT_MODE=supervisor_subagents`. In tests, inject fake Subagents and fake providers. In environments without an LLM, preserve the old deterministic path and mark the result as degraded rather than failing startup.

- [ ] **Step 4: Run focused end-to-end tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_subagent_end_to_end.py tests/test_trip_form_tool_flow.py tests/test_main_agent_end_to_end.py -q`

Expected: PASS.

- [ ] **Step 5: Run the full regression suite**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: PASS with only the repository's existing opt-in external tests skipped.

- [ ] **Step 6: Commit**

```bash
git add app/agents/factory.py app/config.py app/services/main_agent.py tests/test_subagent_end_to_end.py
git commit -m "feat: integrate supervisor subagent planning flow"
```

## Self-Review Checklist

- [ ] Every domain Subagent returns the same typed envelope.
- [ ] Supervisor merges results by `task_id`, not by nondeterministic list position.
- [ ] Weather and transport do not invoke RAG or Deep Search.
- [ ] Attractions, hotel, and food invoke Deep Search only when policy/evaluator requires it.
- [ ] Deep Search has hard round, call, timeout, and read-only limits.
- [ ] Evidence Governance runs before route generation.
- [ ] Existing SSE compatibility, idempotency, safety, and fallback tests remain covered.
