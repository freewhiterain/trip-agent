# GraphRAG Task 6 Report: Wire Graph Evidence Into Attractions And Hotel Workers

## What was implemented

1. Updated the two Chengdu mock Markdown fixtures to add concrete named
   entities and 位于/临近 relation sentences, exactly as specified in the
   brief:
   - `data/documents/attractions/chengdu.md`: added `### 成都大熊猫繁育研究基地`
     (位于成华区), `### 宽窄巷子` (位于青羊区), `### 武侯祠` (位于武侯区), under
     the existing `## 景点主题` bullets, preserving the boilerplate header
     lines and closing disclaimer.
   - `data/documents/accommodation/chengdu.md`: added `### 青羊区住宿片区`
     (临近宽窄巷子) and `### 武侯区住宿片区` (临近武侯祠) under the existing
     `## 住宿选择线索` bullets, preserving the boilerplate header lines and
     closing disclaimer.

2. Added `tests/test_graph_worker_integration.py` (new file) with 3 tests
   covering: merging document + graph evidence for `AttractionsWorker`,
   `AttractionsWorker` behaving normally when the graph service returns
   nothing, and `HotelWorker` returning `unavailable` with empty evidence
   when both document and graph evidence are empty.

3. Modified `app/agents/workers/attractions.py` and
   `app/agents/workers/hotel.py`: added a `graph: GraphKnowledgeService | None
   = None` constructor parameter (defaulting to `None`, so
   `AttractionsWorker(knowledge)` / `HotelWorker(knowledge)` positional calls
   in `registry.py` are unaffected). `run()` now also calls
   `(self.graph or get_graph_knowledge_service()).search_related_entities(...)`
   with the *same* destination/category/query already used for
   `search_destination`, and merges `evidence = [*document_evidence,
   *graph_evidence]` (documents first) before passing to
   `analyze_worker_evidence`. `HotelWorker` still builds its query the same
   way (`f"{task.query} {' '.join(requirement.accommodation_preferences)}"`)
   and passes that same built query to both calls.

`app/agents/workers/registry.py` was not touched, per instructions.

## TDD evidence

### RED (Step 4)

```
.venv\Scripts\python.exe -m pytest tests/test_graph_worker_integration.py -q
```
Result: `3 failed, 1 warning in 4.43s`, all three failures with
`TypeError: AttractionsWorker.__init__() got an unexpected keyword argument 'graph'`
(and the analogous `HotelWorker` TypeError for the third test) — matches the
brief's expected failure exactly.

### GREEN (Step 6)

```
.venv\Scripts\python.exe -m pytest tests/test_graph_worker_integration.py -q
```
Result: `3 passed, 1 warning in 4.35s`.

## Regression test results

### Step 2 — fixture regression (before wiring change)

```
.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py tests/test_phase2_rag_workers.py -q
```
Result: `19 passed, 2 warnings in 14.86s` — no failures; fixture edits did
not break existing boilerplate/metadata assertions.

### Step 7 — Phase 1/2 worker and Supervisor regression (after wiring change)

```
.venv\Scripts\python.exe -m pytest tests/test_phase2_rag_workers.py tests/test_phase1_supervisor.py tests/test_phase2_mock_rag_e2e.py -q
```
Result: `23 passed, 2 warnings in 61.38s` — no failures. Default-constructed
`AttractionsWorker`/`HotelWorker` (via `create_default_registry()`, no
`graph=` argument) call `get_graph_knowledge_service().search_related_entities(...)`,
which hits `GraphKnowledgeService`'s own `try/except` around the DB query
(no reachable/migrated Postgres in this environment) and degrades to an
empty list internally — confirmed no failure surfaced.

## Files changed

- `app/agents/workers/attractions.py` (modified)
- `app/agents/workers/hotel.py` (modified)
- `data/documents/attractions/chengdu.md` (modified)
- `data/documents/accommodation/chengdu.md` (modified)
- `tests/test_graph_worker_integration.py` (new)

## Self-review findings

- Both Workers call `search_related_entities` with the exact same query
  string already passed to `search_destination` — confirmed by reading the
  final source of both files.
- Merged evidence list is `[*document_evidence, *graph_evidence]`
  (documents first) in both Workers — confirmed.
- `HotelWorker` still builds its query as
  `f"{task.query} {' '.join(requirement.accommodation_preferences)}"` and
  passes that same built `query` variable to both `search_destination` and
  `search_related_entities` — confirmed.
- Fixture edits kept the required boilerplate lines (数据类型：模拟资料 /
  适用城市：成都 / 最后更新：开发测试数据) and the closing "不提供实时..."
  disclaimer in both files — confirmed, and Step 2's
  `tests/test_phase2_mock_documents.py` run passed.
- `app/agents/workers/registry.py` was left untouched;
  `create_default_registry()`'s positional `AttractionsWorker(knowledge)` /
  `HotelWorker(knowledge)` calls remain valid since `graph` is a new
  keyword-only-by-position-3 argument with a default of `None`.

No issues found. No out-of-scope files (`weather.py`, `transport.py`,
`food.py`, `registry.py`) were modified.

Note: `.superpowers/sdd/progress.md` had an unrelated pre-existing unstaged
modification (tracking prior GraphRAG Task 1-5 completion) not authored in
this session and out of this task's file scope — left untouched/unstaged,
not included in the commit.

## Commit

`9abad50` — "feat: merge local knowledge graph evidence into attractions and
hotel workers" (5 files changed, 114 insertions, 4 deletions).
