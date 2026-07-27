## Task 7: End-To-End Validation And Documentation

**Files:**
- Test: `tests/test_build_knowledge_graph.py` (one additional opt-in test)
- Modify: `README.md`
- Modify: `.superpowers/sdd/progress.md`

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: one opt-in end-to-end test proving
  "offline extraction -> Postgres write -> Worker query" against the real
  updated Chengdu fixtures; updated project documentation.

- [ ] **Step 1: Write the opt-in end-to-end test**

```python
# append to tests/test_build_knowledge_graph.py
import os

import pytest

from app.agents.workers.attractions import AttractionsWorker
from app.agents.workers.graph_knowledge import GraphKnowledgeService
from app.agents.workers.hotel import HotelWorker
from app.agents.workers.local_knowledge import LocalKnowledgeService
from app.models.base import async_session_maker, init_db
from app.models.knowledge_graph import KnowledgeEntity, KnowledgeRelation
from app.schemas.planning import ResearchTask, TravelRequirement
from datetime import date
from sqlalchemy import select

from scripts.build_knowledge_graph import build_graph


pytestmark_e2e = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_TESTS") != "1",
        reason="requires RUN_POSTGRES_TESTS=1 and a reachable PostgreSQL database",
    ),
]


@pytest.mark.external
@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="requires RUN_POSTGRES_TESTS=1 and a reachable PostgreSQL database",
)
@pytest.mark.asyncio
async def test_real_chengdu_fixtures_produce_queryable_graph_evidence():
    await init_db()
    await build_graph()

    requirement = TravelRequirement(destination="成都", departure_date=date(2026, 9, 1), days=3)
    attractions_result = await AttractionsWorker(knowledge=LocalKnowledgeService()).run(
        ResearchTask(task_type="attractions", query="成都 attractions"), requirement
    )
    hotel_result = await HotelWorker(knowledge=LocalKnowledgeService()).run(
        ResearchTask(task_type="hotel", query="成都 hotel"), requirement
    )

    assert any(item.metadata.get("source_type") == "graph_relation" for item in attractions_result.evidence)
    assert any(item.metadata.get("source_type") == "graph_relation" for item in hotel_result.evidence)
    assert any("临近 宽窄巷子" in item.content for item in hotel_result.evidence)
```

- [ ] **Step 2: Run it (skipped without a live database is expected)**

Run: `.venv\Scripts\python.exe -m pytest tests/test_build_knowledge_graph.py -q`

Expected: `3 passed, 1 skipped` without `RUN_POSTGRES_TESTS=1`; all 4 pass with
it set and a reachable Postgres.

- [ ] **Step 3: Update README**

Add to the "当前实现状态" section of `README.md`, after the existing Phase 2
mock-RAG bullet:

```markdown
- **本地知识图谱（轻量 GraphRAG）**：离线脚本 `scripts/build_knowledge_graph.py`
  从本地 Markdown 资料中抽取实体和"位于/临近"关系，写入 `knowledge_entity`/
  `knowledge_relation` 两张表；`attractions`、`hotel` 两个 Worker 会额外查询
  这层关系图，把结果作为 `source_type="graph_relation"` 的证据与文档证据合并
  使用。抽取只在离线脚本里发生，未配置 LLM 时仅产出规则抽取结果，不阻塞任何
  请求路径；图为空或查询异常时该 Worker 的行为与没有图谱时完全一致。
  `weather`/`transport`/`food` 三个 Worker 暂未接入图证据。
```

- [ ] **Step 4: Update progress.md**

```markdown
Plan: docs/superpowers/plans/2026-07-23-local-graphrag-relations-implementation.md
Local GraphRAG Task 1-7: complete (no commits by instruction unless requested; entities/relations tables + rule/LLM extraction + GraphKnowledgeService + offline build script + attractions/hotel worker integration; opt-in Postgres tests skipped without RUN_POSTGRES_TESTS=1, non-DB tests passed; Phase 1/Phase 2 regression suite unaffected)
```

- [ ] **Step 5: Run the full non-external test suite**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: all previously-passing tests still pass; the new opt-in tests report
as skipped (not failed) without `RUN_POSTGRES_TESTS=1`.

- [ ] **Step 6: Run compileall and the whitespace check**

Run: `.venv\Scripts\python.exe -m compileall -q app scripts tests`

Expected: exit code 0.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 7: Review the worktree without committing (unless the user has asked to commit)**

Run: `git status --short` and `git diff --stat`.

Expected: only the files listed in this plan's File Structure section are
new/modified, plus any pre-existing uncommitted changes from before this plan
started.
