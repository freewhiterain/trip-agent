## Task 6: Wire Graph Evidence Into Attractions And Hotel Workers

**Files:**
- Modify: `app/agents/workers/attractions.py`
- Modify: `app/agents/workers/hotel.py`
- Modify: `data/documents/attractions/chengdu.md`
- Modify: `data/documents/accommodation/chengdu.md`
- Test: `tests/test_graph_worker_integration.py`

**Interfaces:**
- Consumes: `GraphKnowledgeService.search_related_entities` (Task 4).
- Produces: `AttractionsWorker(knowledge=None, llm=None, graph=None)`,
  `HotelWorker(knowledge=None, llm=None, graph=None)` — both merge document
  evidence and graph evidence before calling `analyze_worker_evidence`.

- [ ] **Step 1: Update the Chengdu mock fixtures with named entities**

```markdown
# data/documents/attractions/chengdu.md
# 成都景点模拟资料

数据类型：模拟资料
适用城市：成都
最后更新：开发测试数据

## 景点主题

- 熊猫文化：可作为自然教育与城市文化体验的检索线索。
- 历史街区：适合检索传统建筑、步行游览与本地生活方式主题。
- 博物馆与遗址：适合检索巴蜀历史、文物展示与文化学习主题。

### 成都大熊猫繁育研究基地
位于成华区。是熊猫文化主题下的代表性自然教育地点。

### 宽窄巷子
位于青羊区。是历史街区主题下的代表性步行游览区域。

### 武侯祠
位于武侯区。是博物馆与遗址主题下的代表性文化学习地点。

本资料用于开发测试，不提供实时开放状态、票价、预约名额或营业时间。
```

```markdown
# data/documents/accommodation/chengdu.md
# 成都住宿模拟资料

数据类型：模拟资料
适用城市：成都
最后更新：开发测试数据

## 住宿选择线索

- 选择住宿区域时，可优先比较与计划活动区域的通勤便利度。
- 家庭出行可关注房型空间、洗衣条件和安静程度等偏好。
- 短住行程可把抵达交通和返程衔接纳入位置选择。

### 青羊区住宿片区
临近宽窄巷子。适合安排以历史街区步行游览为主的行程。

### 武侯区住宿片区
临近武侯祠。适合安排以博物馆与遗址主题为主的行程。

本资料为开发测试资料，不提供实时房价、库存、可订状态或服务承诺。
```

- [ ] **Step 2: Run the Phase 2 document/RAG regression tests to confirm the fixture edit does not break existing assertions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py tests/test_phase2_rag_workers.py -q`

Expected: PASS. (These tests assert on the required boilerplate lines and
category metadata, which are unchanged; they do not assert on the exact
bullet content under `## 景点主题`/`## 住宿选择线索`.)

- [ ] **Step 3: Write the failing worker integration tests**

```python
# tests/test_graph_worker_integration.py
from datetime import date

import pytest

from app.agents.workers.attractions import AttractionsWorker
from app.agents.workers.hotel import HotelWorker
from app.agents.workers.local_knowledge import LocalKnowledgeService
from app.schemas.planning import Evidence, ResearchTask, TravelRequirement
from langchain_core.documents import Document


def chengdu_requirement() -> TravelRequirement:
    return TravelRequirement(destination="成都", departure_date=date(2026, 9, 1), days=3)


class _FakeGraphService:
    def __init__(self, evidence: list[Evidence]):
        self._evidence = evidence
        self.calls: list[tuple[str, str, str]] = []

    async def search_related_entities(self, destination, category, query):
        self.calls.append((destination, category, query))
        return self._evidence


def local_knowledge_with_one_attraction() -> LocalKnowledgeService:
    return LocalKnowledgeService(
        documents=[
            Document(
                page_content="### 宽窄巷子\n位于青羊区，适合上午游览。",
                metadata={"source": "attractions/chengdu.md", "city": "成都", "category": "attractions", "source_type": "mock_markdown"},
            ),
        ]
    )


@pytest.mark.asyncio
async def test_attractions_worker_merges_graph_evidence_with_document_evidence():
    graph_evidence = [
        Evidence(
            content="宽窄巷子 位于 青羊区", source="attractions/chengdu.md",
            metadata={"source_type": "graph_relation", "category": "attractions"},
        )
    ]
    graph = _FakeGraphService(graph_evidence)
    worker = AttractionsWorker(knowledge=local_knowledge_with_one_attraction(), graph=graph)

    result = await worker.run(ResearchTask(task_type="attractions", query="成都 attractions"), chengdu_requirement())

    assert result.status in {"completed", "partial"}
    assert any(item.metadata.get("source_type") == "graph_relation" for item in result.evidence)
    assert any(item.metadata.get("source_type") == "mock_markdown" for item in result.evidence)
    assert graph.calls == [("成都", "attractions", "成都 attractions")]


@pytest.mark.asyncio
async def test_attractions_worker_unaffected_when_graph_service_returns_nothing():
    worker = AttractionsWorker(knowledge=local_knowledge_with_one_attraction(), graph=_FakeGraphService([]))

    result = await worker.run(ResearchTask(task_type="attractions", query="成都 attractions"), chengdu_requirement())

    assert result.status == "completed"
    assert all(item.metadata.get("source_type") != "graph_relation" for item in result.evidence)


@pytest.mark.asyncio
async def test_hotel_worker_returns_unavailable_when_both_document_and_graph_evidence_are_empty():
    worker = HotelWorker(knowledge=LocalKnowledgeService(documents=[]), graph=_FakeGraphService([]))

    result = await worker.run(ResearchTask(task_type="hotel", query="成都 hotel"), chengdu_requirement())

    assert result.status == "unavailable"
    assert result.evidence == []
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_worker_integration.py -q`

Expected: FAIL with `TypeError: AttractionsWorker.__init__() got an unexpected keyword argument 'graph'`.

- [ ] **Step 5: Wire the graph service into both Workers**

```python
# app/agents/workers/attractions.py
from app.agents.workers.base import TravelWorker
from app.agents.workers.graph_knowledge import GraphKnowledgeService, get_graph_knowledge_service
from app.agents.workers.local_knowledge import LocalKnowledgeService, get_local_knowledge_service
from app.agents.workers.rag_analysis import analyze_worker_evidence, worker_result_from_analysis
from app.schemas.planning import ResearchTask, TravelRequirement, WorkerResult


class AttractionsWorker(TravelWorker):
    def __init__(
        self,
        knowledge: LocalKnowledgeService | None = None,
        llm=None,
        graph: GraphKnowledgeService | None = None,
    ):
        self.knowledge = knowledge
        self.llm = llm
        self.graph = graph

    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        document_evidence = (self.knowledge or get_local_knowledge_service()).search_destination(
            requirement.destination, "attractions", task.query
        )
        graph_evidence = await (self.graph or get_graph_knowledge_service()).search_related_entities(
            requirement.destination, "attractions", task.query
        )
        evidence = [*document_evidence, *graph_evidence]
        analysis = await analyze_worker_evidence(
            "attractions", task, requirement, evidence, llm=self.llm
        )
        return worker_result_from_analysis(task, "attractions", evidence, analysis)
```

```python
# app/agents/workers/hotel.py
from app.agents.workers.base import TravelWorker
from app.agents.workers.graph_knowledge import GraphKnowledgeService, get_graph_knowledge_service
from app.agents.workers.local_knowledge import LocalKnowledgeService, get_local_knowledge_service
from app.agents.workers.rag_analysis import analyze_worker_evidence, worker_result_from_analysis
from app.schemas.planning import ResearchTask, TravelRequirement, WorkerResult


class HotelWorker(TravelWorker):
    def __init__(
        self,
        knowledge: LocalKnowledgeService | None = None,
        llm=None,
        graph: GraphKnowledgeService | None = None,
    ):
        self.knowledge = knowledge
        self.llm = llm
        self.graph = graph

    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        query = f"{task.query} {' '.join(requirement.accommodation_preferences)}"
        document_evidence = (self.knowledge or get_local_knowledge_service()).search_destination(
            requirement.destination, "hotel", query
        )
        graph_evidence = await (self.graph or get_graph_knowledge_service()).search_related_entities(
            requirement.destination, "hotel", query
        )
        evidence = [*document_evidence, *graph_evidence]
        analysis = await analyze_worker_evidence("hotel", task, requirement, evidence, llm=self.llm)
        return worker_result_from_analysis(task, "hotel", evidence, analysis)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_worker_integration.py -q`

Expected: PASS (3 tests).

- [ ] **Step 7: Run the Phase 1/Phase 2 worker and Supervisor regression tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase2_rag_workers.py tests/test_phase1_supervisor.py tests/test_phase2_mock_rag_e2e.py -q`

Expected: PASS. `create_default_registry()` constructs `AttractionsWorker`/
`HotelWorker` with only `knowledge=` (no `graph=`), so they fall back to
`get_graph_knowledge_service()`, which in these tests either finds no
matching rows (empty city not seeded) or hits a database that has not been
migrated for this table in the test environment — both cases are caught by
`GraphKnowledgeService.search_related_entities`'s own `try/except` and
resolve to an empty list, so existing behavior is unchanged.

- [ ] **Step 8: Commit**

```bash
git add app/agents/workers/attractions.py app/agents/workers/hotel.py \
  data/documents/attractions/chengdu.md data/documents/accommodation/chengdu.md \
  tests/test_graph_worker_integration.py
git commit -m "feat: merge local knowledge graph evidence into attractions and hotel workers"
```

(Skip if the user has asked not to auto-commit.)

---

