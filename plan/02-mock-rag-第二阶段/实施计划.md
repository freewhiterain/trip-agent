# 第二阶段本地模拟 RAG Worker 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用成都本地 Markdown 模拟知识库，完成“RAG 检索 -> Worker Agent 分析 -> Supervisor 汇总 -> 模拟行程草稿”的可测试流程。

**Architecture:** Markdown 只作为知识库原料，由现有文档加载、切片、BM25/Dense/RRF 和重排流程生成证据。五类 Worker 按职责检索证据，再由 Worker Agent 进行结构化分析；Supervisor 只汇总 Worker 结果，不直接读取 Markdown，也不生成没有证据支持的事实。

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, LangChain documents, BM25, Chroma-compatible retriever, LangGraph Supervisor, vanilla HTML/JavaScript, pytest.

## Global Constraints

- 第二阶段只使用成都本地 Markdown 模拟资料，不调用天气、地图、航班、铁路、酒店或餐厅真实 API。
- Markdown 是知识库原料，不是 Worker 的直接输出；Worker 必须先检索证据，再进行 Agent 分析。
- Worker 不得编造检索证据中不存在的价格、班次、营业状态、库存或天气事实。
- LLM 不可用时，Worker 必须使用确定性证据摘要降级，不阻塞 Supervisor。
- 所有结果必须明确标记模拟资料，并保留来源文件和引用证据。
- 缺少对应资料或检索无命中时，Worker 使用 `unavailable`；已有部分证据但无法形成完整建议时使用 `partial`。
- 单个 Worker 失败时其他 Worker 继续执行；Supervisor 只执行一次并保留幂等边界。
- 直接在当前 `main` 工作区修改，不创建分支，不覆盖已有工作区修改，不自动提交 commit。

---

## File Structure

- Create: `data/documents/attractions/chengdu.md`：成都景点模拟资料。
- Create: `data/documents/weather/chengdu.md`：成都天气建议模拟资料，明确非实时。
- Create: `data/documents/transport/chengdu.md`：成都交通模拟资料，避免虚构班次和价格。
- Create: `data/documents/accommodation/chengdu.md`：成都住宿区域和类型模拟资料。
- Create: `data/documents/food/chengdu.md`：成都美食模拟资料。
- Modify: `app/rag/document_loader.py`：为文档补充城市、职责类别和模拟资料元数据。
- Modify: `app/agents/workers/local_knowledge.py`：提供带城市、类别和查询条件的 RAG 检索接口。
- Create: `app/agents/workers/rag_analysis.py`：统一 Worker Agent 的结构化分析和无 LLM 降级。
- Modify: `app/agents/workers/attractions.py`、`weather.py`、`transport.py`、`hotel.py`、`food.py`：改为 RAG + Agent Worker。
- Modify: `app/agents/workers/registry.py`：注册统一的本地 RAG Worker 配置。
- Modify: `app/schemas/planning.py`：为 `WorkerResult` 增加模拟资料标记和必要的结构化字段。
- Modify: `app/agents/supervisor.py`：保留 Worker 结果、来源、警告和部分完成状态。
- Modify: `1_zhixing.html`：展示 Worker 状态、模拟资料提示、证据来源和警告。
- Create: `tests/test_phase2_mock_documents.py`：验证成都资料加载和类别隔离。
- Create: `tests/test_phase2_rag_workers.py`：验证 RAG 检索、Agent 分析和降级行为。
- Modify: `tests/test_phase1_supervisor.py`：补充部分 Worker 失败和模拟标记断言。
- Create: `tests/test_phase2_mock_rag_e2e.py`：验证表单到模拟行程草稿的完整流程。

## Task 1: Add Chengdu Mock Knowledge Documents

**Files:**
- Create: `data/documents/attractions/chengdu.md`
- Create: `data/documents/weather/chengdu.md`
- Create: `data/documents/transport/chengdu.md`
- Create: `data/documents/accommodation/chengdu.md`
- Create: `data/documents/food/chengdu.md`
- Modify: `app/rag/document_loader.py`
- Test: `tests/test_phase2_mock_documents.py`

**Interfaces:**
- Consumes: Markdown files under `data/documents/`.
- Produces: Documents with `city="成都"`, Worker category, and `source_type="mock_markdown"` metadata.

- [ ] **Step 1: Write the failing metadata test**

```python
def test_chengdu_mock_documents_have_worker_metadata():
    documents = DocumentManager().load_all_documents()
    chengdu = [item for item in documents if item.metadata.get("city") == "成都"]
    categories = {item.metadata.get("category") for item in chengdu}
    assert {"attractions", "weather", "transport", "hotel", "food"} <= categories
    assert all(item.metadata.get("source_type") == "mock_markdown" for item in chengdu)
```

- [ ] **Step 2: Run the focused test**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py::test_chengdu_mock_documents_have_worker_metadata -q`

Expected: FAIL because the category fixtures and metadata mapping are incomplete.

- [ ] **Step 3: Add the five concise Markdown fixtures**

Each file must start with the following declaration and then use stable headings suitable for chunking:

```markdown
# 成都景点模拟资料

数据类型：模拟资料
适用城市：成都
最后更新：开发测试数据

## 景点
### 示例景点
- 适合时段：上午
- 建议时长：2 小时
- 说明：用于验证 RAG 检索和行程编排。
```

Use the same explicit structure for weather advice, transport guidance, accommodation areas, and food recommendations. Do not write live prices, live schedules, inventory, or current weather claims.

- [ ] **Step 4: Normalize loader metadata**

Keep `DocumentManager.load_all_documents() -> list[Document]` intact. Derive the category from the directory and the city from the filename or header. Map the `accommodation/` directory to Worker category `hotel`. Set `source_type="mock_markdown"` for these fixtures and preserve unrelated existing metadata.

- [ ] **Step 5: Run document and existing RAG tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py tests/test_phase2_rag.py -q`

Expected: PASS.

## Task 2: Add Category-Scoped RAG Retrieval

**Files:**
- Modify: `app/agents/workers/local_knowledge.py`
- Test: `tests/test_phase2_rag_workers.py`

**Interfaces:**
- Consumes: `destination: str`, `category: TaskType`, and a Worker query string.
- Produces: `LocalKnowledgeService.search_destination(destination, category, query) -> list[Evidence]`.

- [ ] **Step 1: Write the failing category-isolation test**

```python
def test_destination_search_only_returns_requested_category(monkeypatch):
    service = build_fixture_knowledge_service(monkeypatch)
    results = service.search_destination("成都", "attractions", "适合上午的景点")
    assert results
    assert all(item.metadata.get("category") == "attractions" for item in results)
```

- [ ] **Step 2: Run the focused test**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_rag_workers.py::test_destination_search_only_returns_requested_category -q`

Expected: FAIL because category-scoped retrieval is not exposed.

- [ ] **Step 3: Implement the category-scoped method**

Add `search_destination` without removing the existing `search` method. Filter loaded/chunked documents by normalized city and category before querying. Build the query from destination, category, and the supplied Worker query. Convert retrieved documents to `Evidence` while preserving source metadata.

- [ ] **Step 4: Add explicit empty-result behavior**

Return an empty list when the city/category has no documents. Never fall back to another category just to produce content; Worker code will turn the empty result into `unavailable`.

- [ ] **Step 5: Run focused RAG tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py tests/test_phase2_rag_workers.py -q`

Expected: PASS.

## Task 3: Implement Evidence-Bound Worker Agent Analysis

**Files:**
- Create: `app/agents/workers/rag_analysis.py`
- Modify: `app/schemas/planning.py`
- Test: `tests/test_phase2_rag_workers.py`

**Interfaces:**
- Consumes: `worker: TaskType`, `ResearchTask`, `TravelRequirement`, and `list[Evidence]`.
- Produces: `WorkerAnalysis` with `summary`, `options`, `warnings`, and `used_mock_data`.
- Produces: `async analyze_worker_evidence(worker, task, requirement, evidence) -> WorkerAnalysis`.

- [ ] **Step 1: Define the structured analysis test**

```python
async def test_worker_analysis_returns_evidence_backed_options():
    result = await analyze_worker_evidence(
        "attractions",
        ResearchTask(task_type="attractions", query="成都 景点"),
        chengdu_requirement(),
        [Evidence(content="示例景点适合上午游览", source="data/documents/attractions/chengdu.md")],
        llm=fake_structured_llm(),
    )
    assert result.used_mock_data is True
    assert result.options[0].name == "示例景点"
```

- [ ] **Step 2: Define the no-evidence fallback test**

```python
async def test_worker_analysis_does_not_invent_content_without_evidence():
    result = await analyze_worker_evidence(
        "weather",
        ResearchTask(task_type="weather", query="成都天气"),
        chengdu_requirement(),
        [],
        llm=None,
    )
    assert result.options == []
    assert result.warnings
```

- [ ] **Step 3: Implement the structured response boundary**

Define a Pydantic response model containing only a short summary, candidate options, and warnings. The system instruction must require the model to use only supplied evidence and mark unverified/live fields unavailable. Do not return hidden chain-of-thought; retain only concise rationale and evidence references.

- [ ] **Step 4: Implement deterministic fallback**

When no LLM is configured or the structured call fails, derive concise options from evidence headings/content, set `used_mock_data=True`, and add a warning that the result is an evidence summary rather than a live recommendation. With no evidence, return no options and a data-unavailable warning.

- [ ] **Step 5: Add the result marker**

Add `unavailable` to `WorkerStatus` and `is_mock: bool = False` to `WorkerResult`. Local Markdown Worker results set `is_mock=True`; the default keeps external result compatibility.

- [ ] **Step 6: Run analysis tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_rag_workers.py -q`

Expected: PASS.

## Task 4: Convert All Five Workers To RAG + Agent

**Files:**
- Modify: `app/agents/workers/attractions.py`
- Modify: `app/agents/workers/weather.py`
- Modify: `app/agents/workers/transport.py`
- Modify: `app/agents/workers/hotel.py`
- Modify: `app/agents/workers/food.py`
- Modify: `app/agents/workers/registry.py`
- Test: `tests/test_phase2_rag_workers.py`

**Interfaces:**
- Consumes: existing `TravelWorker.run(task, requirement)`.
- Produces: `WorkerResult` with RAG evidence, Agent-derived options, `is_mock=True`, and warnings.

- [ ] **Step 1: Add one contract test per Worker**

```python
@pytest.mark.parametrize("worker_name", ["attractions", "weather", "transport", "hotel", "food"])
async def test_local_worker_uses_category_rag(worker_name, monkeypatch):
    registry = build_local_registry(monkeypatch)
    result = await registry.run(
        ResearchTask(task_type=worker_name, query=f"成都 {worker_name}"),
        chengdu_requirement(),
    )
    assert result.is_mock is True
    assert result.evidence or result.status == "unavailable"
```

- [ ] **Step 2: Run the worker tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_rag_workers.py -q`

Expected: FAIL for Workers that still use external adapters or unconditional placeholder options.

- [ ] **Step 3: Inject local RAG and Agent analysis**

Keep `run(task, requirement) -> WorkerResult`. Each Worker builds a category-specific query from the task, destination, dates, days, and preferences; calls `search_destination`; passes evidence to `analyze_worker_evidence`; and maps the analysis into `WorkerResult`.

- [ ] **Step 4: Keep external integrations disabled**

Do not delete external adapters. Ensure `create_default_registry(enable_external=False)` creates local RAG Workers and makes no weather or web-search calls. Preserve the external branch for the later data-source phase.

- [ ] **Step 5: Remove unsupported concrete placeholders**

Transport and hotel must return `unavailable` when no local evidence exists instead of inventing options. Use `partial` only when evidence exists but cannot support a complete recommendation. No Worker may return a concrete option with an empty evidence list.

- [ ] **Step 6: Run Worker and legacy contract tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_rag_workers.py tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py -q`

Expected: PASS.

## Task 5: Preserve Supervisor Statuses And Evidence

**Files:**
- Modify: `app/agents/supervisor.py`
- Modify: `app/agents/worker_tools.py` if event payloads omit `is_mock` or evidence.
- Modify: `tests/test_phase1_supervisor.py`
- Create: `tests/test_phase2_mock_rag_e2e.py`

**Interfaces:**
- Consumes: five `WorkerResult` values from the local RAG registry.
- Produces: `TravelPlanDraft` with statuses, flattened evidence, warnings, and mock markers.

- [ ] **Step 1: Add the aggregation test**

```python
def test_assembled_draft_keeps_mock_evidence_and_warnings():
    draft = assemble_draft(requirement, [completed_mock_result, partial_result], itinerary, budget)
    assert draft.evidence
    assert draft.warnings == ["交通模拟资料暂未配置"]
    assert all(result.is_mock for result in draft.worker_results)
```

- [ ] **Step 2: Run the Supervisor test**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase1_supervisor.py -q`

Expected: the new mock-marker assertion fails until the output contract carries it through.

- [ ] **Step 3: Preserve statuses in events and draft output**

Ensure Worker start, complete, partial, and failed events include Worker name and status. Keep every Worker result in `TravelPlanDraft.worker_results`; flatten evidence without discarding source metadata.

- [ ] **Step 4: Keep synthesis evidence-constrained**

The deterministic template or configured LLM synthesis may use only Worker options and evidence. Keep generic placeholders for unsupported fields and retain warnings in the final draft.

- [ ] **Step 5: Run Supervisor tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase1_supervisor.py tests/test_phase2_mock_rag_e2e.py -q`

Expected: PASS.

## Task 6: Show Mock RAG Progress And Sources

**Files:**
- Modify: `1_zhixing.html`
- Test: `tests/test_frontend_trip_form.py`
- Test: `tests/test_phase2_mock_rag_e2e.py`

**Interfaces:**
- Consumes: existing SSE Worker events and final `TravelPlanDraft` payload.
- Produces: visible Worker progress, `本地模拟资料` label, source details, and warnings.

- [ ] **Step 1: Add frontend contract assertions**

```python
def test_frontend_contains_mock_rag_status_and_source_rendering():
    html = Path("1_zhixing.html").read_text(encoding="utf-8")
    assert "本地模拟资料" in html
    assert "worker_started" in html
    assert "evidence" in html
    assert "warnings" in html
```

- [ ] **Step 2: Run the focused frontend test**

Run: `.venv\Scripts\python.exe -m pytest tests/test_frontend_trip_form.py::test_frontend_contains_mock_rag_status_and_source_rendering -q`

Expected: FAIL until Worker status and evidence rendering are present.

- [ ] **Step 3: Render stable five-row Worker status**

Update the existing SSE handler so Worker start, complete, partial, and failed events update one stable status row for each Worker. Do not submit status updates as chat messages or create another planning form.

- [ ] **Step 4: Render mock label, evidence, and warnings**

Show `本地模拟资料` at the result beginning. For each Worker, show summary, status, warnings, source path, and evidence content in a collapsible details region. Keep unavailable data visually distinct.

- [ ] **Step 5: Verify history and refresh rendering**

Saved final draft payloads must render the same mock label and evidence sections after history reload. Preserve pending Tool Call restoration behavior.

- [ ] **Step 6: Run frontend and API tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_frontend_trip_form.py tests/test_trip_form_tool_flow.py tests/test_phase2_mock_rag_e2e.py -q`

Expected: PASS.

## Task 7: Add End-To-End Coverage And Documentation

**Files:**
- Create: `tests/test_phase2_mock_rag_e2e.py`
- Modify: `README.md`
- Modify: `docs/superpowers/NEW_CHAT_HANDOFF.md` only if the progress ledger is updated there.

**Interfaces:**
- Consumes: Main Agent Tool Result, Supervisor, local RAG registry, and frontend event contracts.
- Produces: deterministic coverage for the complete Chengdu mock-data workflow.

- [ ] **Step 1: Test the complete local-data workflow**

Cover:

```text
complete form
  -> Supervisor exactly once
  -> five category-scoped RAG queries
  -> five Worker Agent results
  -> evidence-backed mock draft
```

- [ ] **Step 2: Test missing data and LLM failure**

Remove one category fixture and force the structured LLM call to fail. Assert the draft still contains other Worker results, includes a warning, and contains no unsupported concrete option.

- [ ] **Step 3: Update README status**

Document the Chengdu-only local mock RAG phase, the difference between Markdown source and Worker Agent analysis, the `is_mock` marker, and the fact that real data sources and retrieval-mode changes are deferred to the next phase.

- [ ] **Step 4: Run focused regression tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py tests/test_phase2_rag_workers.py tests/test_phase2_mock_rag_e2e.py tests/test_phase1_supervisor.py tests/test_frontend_trip_form.py -q`

Expected: PASS.

- [ ] **Step 5: Run full verification**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: all applicable tests PASS; documented PostgreSQL tests may remain skipped only when prerequisites are unavailable.

Run: `.venv\Scripts\python.exe -m compileall -q app tests`

Expected: exit code 0.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 6: Review the worktree without committing**

Run: `git status --short` and `git diff --stat`.

Expected: only approved Phase 2 files and pre-existing user changes are present. Do not reset or discard unrelated changes.
