# Phase 2 Task 2 Report

## Scope

Implemented category-scoped local RAG retrieval in `LocalKnowledgeService` while preserving the existing global `search(query)` API used by open travel Q&A.

## TDD Evidence

- RED: `D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest tests\test_phase2_rag_workers.py -q`
  - Result: `1 failed, 1 warning in 9.18s`
  - Expected failure: `TypeError: LocalKnowledgeService.__init__() got an unexpected keyword argument 'documents'`.
- GREEN (isolation test): the same focused command passed with `1 passed, 1 warning in 10.57s`.
- GREEN (required Phase 2 suite): `D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest tests\test_phase2_mock_documents.py tests\test_phase2_rag_workers.py tests\test_phase2_rag.py -q`
  - Result: `10 passed, 1 warning in 9.90s`.

## Implementation

- Added constructor injection of real source `Document` objects for production-compatible local source configuration.
- Added `search_destination(destination, category, query)`, filtering raw source documents by trimmed, case-normalized `city` and `category` before splitting and retrieval.
- Scoped retrieval uses `destination`, `category`, and `query` together, returns an empty list when no documents match, and preserves document source and metadata through `Evidence` conversion.
- Added real retrieval tests for category isolation, empty category results, and legacy global search behavior.

## Self-Review

- Verified no fallback to a different category occurs because the scoped retriever is built only from matching documents.
- Verified retrieval uses no vector store or external API calls.
- Ran scoped `git diff --check` successfully; no whitespace errors found.
- No issues found in the changed code.

## Files Changed

- `app/agents/workers/local_knowledge.py`
- `tests/test_phase2_rag_workers.py`
- `.superpowers/sdd/phase2-task-2-report.md`

## Concern

The required test run emits one pre-existing dependency warning from `jieba` about deprecated `pkg_resources`; it does not affect test results.

## Empty-Corpus Review Fix

### Root Cause

`LocalKnowledgeService` eagerly built `HybridRetriever` at construction. When injected documents were empty, or when a matching blank document split into zero child chunks, `HybridRetriever` initialized `BM25Okapi([])`. That raised `ZeroDivisionError` while calculating the average document length.

### TDD Evidence

RED command:

```powershell
D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest tests\test_phase2_rag_workers.py -q
```

RED output:

```text
...FF                                                                    [100%]
FAILED tests/test_phase2_rag_workers.py::test_search_destination_returns_empty_for_an_empty_injected_corpus
FAILED tests/test_phase2_rag_workers.py::test_search_destination_returns_empty_when_matching_document_has_no_content
2 failed, 3 passed, 1 warning in 10.30s
```

The failing stack traces identify `ZeroDivisionError: division by zero` from `rank_bm25.BM25Okapi` after the splitter produced zero parent and child documents.

GREEN focused command:

```powershell
D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest tests\test_phase2_rag_workers.py -q
```

GREEN focused output:

```text
.....                                                                    [100%]
5 passed, 1 warning in 8.96s
```

GREEN full Task 2 and RAG command:

```powershell
D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest tests\test_phase2_mock_documents.py tests\test_phase2_rag_workers.py tests\test_phase2_rag.py -q
```

GREEN full output:

```text
............                                                             [100%]
12 passed, 1 warning in 4.61s
```

### Fix Details

- `_build_retriever` returns `None` when splitting produces zero child chunks, avoiding empty BM25 initialization.
- Global `search` and scoped `search_destination` return `[]` when no retriever can be built.
- Added regression tests for an empty injected corpus and a matching blank document.
