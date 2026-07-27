# GraphRAG Final Cross-Cutting Fix Report

Branch: `main` (working directly on main, as authorized). Base commit: `e41f0da`.
Fix commit: `7cc979c` — "Fix cross-cutting GraphRAG review findings from final branch review".

## Findings Fixed

### Important #1 — `build_graph()` never ensures schema exists

File: `scripts/build_knowledge_graph.py`

Before (relevant lines, at `e41f0da`):
```python
from typing import Callable
...
async def build_graph(
    *,
    document_manager: DocumentManager | None = None,
    service_factory: Callable[[], GraphKnowledgeService] = GraphKnowledgeService,
    llm_factory: Callable[[], object] | None = None,
) -> None:
    document_manager = document_manager or DocumentManager()
    ...
```
No call to `init_db()` anywhere in `build_graph()` or `main()`.

After (`scripts/build_knowledge_graph.py:9`, `:13`, `:29-31`):
```python
from typing import Awaitable, Callable

from app.agents.workers.graph_knowledge import GraphKnowledgeService
from app.config import settings
from app.models.base import init_db
...
async def build_graph(
    *,
    document_manager: DocumentManager | None = None,
    service_factory: Callable[[], GraphKnowledgeService] = GraphKnowledgeService,
    llm_factory: Callable[[], object] | None = None,
    ensure_schema: Callable[[], Awaitable[None]] = init_db,
) -> None:
    await ensure_schema()
    document_manager = document_manager or DocumentManager()
```
`main()` (unchanged, still `asyncio.run(build_graph())`) now transitively ensures schema
via the new default parameter — no separate `init_db()` call needed in `main()`.

Test-side fix (required to avoid the 3 existing fake-service tests suddenly hitting a
real DB): `tests/test_build_knowledge_graph.py:28-29` adds
```python
async def _noop_ensure_schema():
    return None
```
and all three pre-existing `await build_graph(...)` calls
(`test_build_graph_writes_rule_extracted_relations_without_llm`,
`test_build_graph_skips_llm_extraction_when_not_configured`,
`test_build_graph_continues_when_llm_extraction_fails`) now pass
`ensure_schema=_noop_ensure_schema` (lines 40-44, 60-65, 80-85).

The opt-in e2e test `test_real_chengdu_fixtures_produce_queryable_graph_evidence`
(line 108) was left calling `await init_db()` then `await build_graph()` as-is — calling
`init_db()` twice is harmless (idempotent `create_all`), and the instructions explicitly
allowed leaving it unchanged.

### Important #2 — `Evidence` construction outside try/except

File: `app/agents/workers/graph_knowledge.py`

Before (`e41f0da`, lines ~97-127): the `targets = (...)` query was the last statement
inside `try:`, then `except Exception as exc: return []` closed the block, and *after*
that (outside try/except) came `entities_by_id = ...`, `targets_by_id = ...`,
`evidence: list[Evidence] = []`, and the `for relation in relations: ... Evidence(...)`
loop, ending in `return evidence`.

After (`app/agents/workers/graph_knowledge.py:97-128`): the evidence-building block
(`entities_by_id`, `targets_by_id`, `evidence = []`, and the `for relation in relations`
loop building `Evidence(...)`) was moved to sit directly under the `targets = (...)`
query, still inside the same `try:` block, immediately before `except Exception as exc:`
(line 124). The final `return evidence` (line 128) remains outside/after the
try/except, executed only when the try block completes without raising. Any exception
raised anywhere in query execution *or* evidence construction is now caught by the same
handler and degrades to `return []`, matching the stated invariant that
`search_related_entities` never raises.

### Important #3 — design spec 1-2 hop vs. 1-hop implementation (docs only)

File: `docs/superpowers/specs/2026-07-23-local-graphrag-relations-design.md`

- Line 35 (包含 section): changed "按城市+类别做 1-2 跳查询" to "按城市+类别做 1 跳查询".
- Lines 98-102 (检索集成 section): removed the clause describing 2-hop expansion
  through `area`-category targets ("以及关系另一端实体的直接关系（2 跳，仅当另一端是
  `area` 类实体时展开，避免跳数爆炸）") and added an explicit sentence stating the
  current implementation is 1-hop only and that 2-hop `area` expansion is deferred to a
  later iteration, consistent with the doc's "轻量先行、按需扩展" framing.
- Left line 44 ("不包含" section: `跨文档多跳（>2 跳）遍历`) and lines 148-150
  ("后续阶段" section mentioning "2 跳查询" as a still-hypothetical future scaling
  trigger) unchanged — both already correctly describe >2-hop / future 2-hop as
  out of scope, so they were already consistent with the 1-hop reality and did not need
  correction.
- No code changes made for this finding, as instructed.

## Minor Cleanup Fixed

1. `tests/test_graph_extraction.py` — `test_resolve_relations_links_near_when_target_is_known`
   (was line 110: `assert resolved[0] == resolved[0]`) replaced with
   `assert resolved[0].from_category == "hotel"` and
   `assert resolved[0].to_category == "attractions"` (now lines 110-111).
2. `tests/test_build_knowledge_graph.py` — removed the dead `pytestmark_e2e` list
   variable (was lines 95-101 at `e41f0da`).
3. `tests/test_build_knowledge_graph.py` — removed now-unused imports
   `GraphKnowledgeService`, `async_session_maker`, `KnowledgeEntity`,
   `KnowledgeRelation`, `select` (were lines 85, 88-89, 92 at `e41f0da`); kept `init_db`
   (used by the e2e test) and all other still-used imports.
4. `tests/test_graph_knowledge_service_postgres.py` — removed unused
   `from uuid import uuid4` (was line 2).

## Test Output

### Covering tests (combined single run)
```
.venv\Scripts\python.exe -m pytest -q tests/test_build_knowledge_graph.py tests/test_graph_extraction.py tests/test_graph_knowledge_service.py tests/test_graph_knowledge_service_postgres.py tests/test_phase2_rag_workers.py tests/test_phase1_supervisor.py tests/test_phase2_mock_rag_e2e.py tests/test_graph_worker_integration.py

...s...........ss..........................                              [100%]
============================== warnings summary ===============================
.venv\Lib\site-packages\jieba\_compat.py:18
  UserWarning: pkg_resources is deprecated as an API...
.venv\Lib\site-packages\langgraph\cache\base\__init__.py:8
  LangChainPendingDeprecationWarning: The default value of `allowed_objects`...
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
40 passed, 3 skipped, 2 warnings in 61.06s (0:01:01)
```
The 3 skips are the opt-in Postgres/e2e tests in `test_graph_knowledge_service_postgres.py`
(2 tests) and `test_build_knowledge_graph.py` (1 test), all gated on
`RUN_POSTGRES_TESTS=1` with a reachable database, which is not available in this
environment — expected.

### Full suite
```
.venv\Scripts\python.exe -m pytest -q

...s...........ss...............ss...................................... [ 42%]
........................................................................ [ 85%]
....s....................                                                [100%]
163 passed, 6 skipped, 2 warnings in 104.04s (0:01:44)
```
Matches the stated pre-fix baseline exactly: 163 passed, 6 skipped, 0 failures.

### compileall
```
.venv\Scripts\python.exe -m compileall -q app scripts tests
```
Exit code: 0, no output.

### git diff --check
```
git diff --check
```
Exit code: 0. (Only CRLF/LF line-ending advisory warnings printed to stderr from Git's
autocrlf handling on Windows — not whitespace errors; no actual `--check` violations
reported.)

## Files Changed (in commit 7cc979c)

- `app/agents/workers/graph_knowledge.py`
- `scripts/build_knowledge_graph.py`
- `tests/test_build_knowledge_graph.py`
- `tests/test_graph_extraction.py`
- `tests/test_graph_knowledge_service_postgres.py`
- `docs/superpowers/specs/2026-07-23-local-graphrag-relations-design.md`

## Self-Review

- Re-read the full diff of `graph_knowledge.py` and `build_knowledge_graph.py` after
  editing (via `git diff e41f0da HEAD -- ...`) to confirm indentation/scope is correct:
  the evidence-building loop is genuinely inside the `try:` block now (same indent level
  as the preceding `targets = (...)` statement), and `return evidence` is genuinely
  outside/after the `except` clause.
- Confirmed via test run that all three previously-passing fake-service
  `test_build_graph_*` tests still pass without attempting a real DB connection (would
  have hung/failed in this environment otherwise) — they do, in ~61s combined for the
  8-file covering run, well within normal bounds (no DB-connection-timeout style delay).
- Confirmed `test_graph_knowledge_service.py` (the non-DB error-path test for finding #2)
  and the Postgres-marked round-trip test both still pass/skip as expected — the
  reordering did not change any user-visible behavior when queries succeed.
- Confirmed the 4 regression test files (`test_phase2_rag_workers.py`,
  `test_phase1_supervisor.py`, `test_phase2_mock_rag_e2e.py`,
  `test_graph_worker_integration.py`) are unaffected — full covering run shows all pass.
- Verified full-suite counts (163 passed / 6 skipped) match the given baseline exactly,
  confirming zero regressions and zero new failures introduced.
- Noticed `.superpowers/sdd/progress.md` was already modified in the working tree at the
  start of this session (not by any edit I made) — left it out of my commit since it's
  unrelated to the 3 findings + 4 minor items I was asked to fix; it remains as an
  uncommitted, pre-existing local change.

## Issues or Concerns

None. All required fixes applied, all covering tests and the full suite pass with the
expected pass/skip counts, `compileall` and `git diff --check` both clean, single commit
created directly on `main` as authorized.
