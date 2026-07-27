# GraphRAG Task 5: Offline Build Script — Report

## What was implemented

- `scripts/__init__.py` — new empty file (did not previously exist), making
  `scripts` an importable package for the test module.
- `scripts/build_knowledge_graph.py` — offline orchestration entry point:
  - `async build_graph(*, document_manager=None, service_factory=GraphKnowledgeService, llm_factory=None) -> None`
    - Loads documents via `DocumentManager().load_all_documents()` (or the
      injected `document_manager`), filters to
      `metadata["source_type"] == "mock_markdown"`.
    - Runs rule-based extraction via `extract_from_documents(documents)`.
    - Only enters the LLM code path when `settings.dashscope_api_key` is
      truthy; inside that branch, lazily imports `app.agents.llm.get_llm` as
      the default `llm_factory` only if none was injected, wraps
      `llm_factory()` in try/except (logs a warning and falls back to
      `llm=None` on failure), and — when an LLM instance was obtained — runs
      `extract_relations_with_llm` per document and extends `relations`.
    - Groups entities by city, and for each city calls
      `resolve_relations(city, entities, relations)` passing the **full**
      relations list (not a pre-filtered per-city subset), relying on
      `resolve_relations`'s own `relation.city != city` filter.
    - Writes each city's `entities + extra_entities` and `resolved` relations
      via `service.write_entities_and_relations(...)`, with an info log line
      per city.
  - `main()` — sync entry point (`asyncio.run(build_graph())`) for
    `python scripts/build_knowledge_graph.py`.
- `tests/test_build_knowledge_graph.py` — the three tests exactly as given in
  the task brief, using `_StubDocumentManager` and `_RecordingGraphService`
  test doubles plus `monkeypatch` on `scripts.build_knowledge_graph.settings.dashscope_api_key`.

Implementation matches the brief's code verbatim; no deviations were needed —
cross-checked `app/rag/graph_extraction.py`, `app/agents/workers/graph_knowledge.py`,
`app/rag/document_loader.py`, and `app/agents/llm.py` (`get_llm`) beforehand
and all signatures/behaviors matched what the brief assumed.

## TDD evidence

### RED (Step 2, before implementing `scripts/build_knowledge_graph.py`)

```
$ .venv\Scripts\python.exe -m pytest tests/test_build_knowledge_graph.py -q
=================================== ERRORS ====================================
____________ ERROR collecting tests/test_build_knowledge_graph.py _____________
ImportError while importing test module 'D:\Desktop\project\Trip\tests\test_build_knowledge_graph.py'.
...
E   ModuleNotFoundError: No module named 'scripts.build_knowledge_graph'
=========================== short test summary info ===========================
ERROR tests/test_build_knowledge_graph.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
1 error in 0.22s
```

Failed for the expected reason (module not found), exactly as specified in
the brief's Step 2.

### GREEN (Step 5, after implementing the script)

```
$ .venv\Scripts\python.exe -m pytest tests/test_build_knowledge_graph.py -q
...                                                                      [100%]
============================== warnings summary ===============================
.venv\Lib\site-packages\jieba\_compat.py:18
  ...: UserWarning: pkg_resources is deprecated as an API. ...
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
3 passed, 1 warning in 4.89s
```

3/3 passed. The single warning is a pre-existing, unrelated `jieba`
deprecation warning that appears across the whole suite (not introduced by
this change).

### Full suite regression check

```
$ .venv\Scripts\python.exe -m pytest -q
...
160 passed, 5 skipped, 2 warnings in 32.20s
```

No regressions; the 5 skips are pre-existing opt-in PostgreSQL tests. Both
warnings shown are pre-existing (jieba `pkg_resources`, langgraph
`LangChainPendingDeprecationWarning`), unrelated to this task's files.

## Files changed

- `D:\Desktop\project\Trip\scripts\__init__.py` (new, empty)
- `D:\Desktop\project\Trip\scripts\build_knowledge_graph.py` (new)
- `D:\Desktop\project\Trip\tests\test_build_knowledge_graph.py` (new)

Commit: `185d37a` — "feat: add offline knowledge graph build script"
(only the three files above were staged and committed; an unrelated
pre-existing local modification to `.superpowers/sdd/progress.md` — tracking
notes from earlier tasks in this plan, not part of this task's scope — was
left uncommitted).

## Self-review findings

Checked against the review checklist in the task instructions:

1. **`llm_factory` never invoked when `dashscope_api_key` is empty/falsy** —
   confirmed. The entire LLM branch, including the `llm_factory()` call, is
   inside `if settings.dashscope_api_key:`; when falsy, execution goes
   straight to the `else` branch (log-and-skip). Verified directly by test
   `test_build_graph_skips_llm_extraction_when_not_configured`, which asserts
   `llm_factory_calls == []`.
2. **LLM client construction failure logs a warning and continues with
   `llm=None`** — confirmed via the `try/except Exception as exc` around
   `llm_factory()`, which logs
   `f"初始化 LLM 失败，跳过 LLM 补充抽取：{type(exc).__name__}: {exc}"` and sets
   `llm = None`; the subsequent `if llm is not None:` guard skips the
   extraction loop. Verified by
   `test_build_graph_continues_when_llm_extraction_fails`, which uses an
   `_ExplodingLlm` factory and asserts the rule-extracted relation is still
   written (`len(relations) == 1`).
3. **Per-city grouping passes the FULL relations list into
   `resolve_relations`** — confirmed: `relations` accumulates all
   rule-extracted (and, if applicable, LLM-extracted) relations across every
   document/city before the per-city loop begins, and
   `resolve_relations(city, entities, relations)` is called with that same
   full list on every iteration — `resolve_relations`'s own
   `relation.city != city` filter does the per-city selection, as intended.
4. **Test output pristine** — 3/3 pass with no stdout/stderr noise beyond a
   pre-existing unrelated dependency warning; full-suite run confirms no
   regressions (160 passed, 5 skipped).

No deviations from the brief were made and no issues were found.

## Concerns

None. The task was self-contained, all consumed interfaces from Tasks 1-4
matched the brief's assumptions exactly, and no changes were needed to any
Task 1-4 file.
