# Task 2: Add Category-Scoped RAG Retrieval

## Files

- Modify: `app/agents/workers/local_knowledge.py`
- Create or modify: `tests/test_phase2_rag_workers.py`

## Interface

Add:

```python
LocalKnowledgeService.search_destination(
    destination: str,
    category: TaskType,
    query: str,
) -> list[Evidence]
```

Keep the existing `search(query: str) -> list[Evidence]` method working for open travel Q&A.

## Requirements

- Limit candidate documents by normalized metadata `city` and Worker `category` before retrieval.
- A request for one Worker category must not return another category's evidence.
- Build retrieval text from destination, category, and the supplied query.
- Preserve evidence source and metadata.
- Return `[]` when there are no matching city/category documents.
- Do not fall back to another category and do not call external APIs.
- Keep the retrieval implementation behind `LocalKnowledgeService` so a later phase can replace the RAG mode.
- Support dependency injection of source documents or equivalent test setup without adding test-only production APIs.

## TDD

1. Add a failing category-isolation test using real `Document` objects and real retrieval behavior.
2. Run the focused test and record RED.
3. Implement the minimal scoped retrieval method.
4. Add/verify the empty-category test and the existing global `search` compatibility test.
5. Run:

```powershell
D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py tests/test_phase2_rag_workers.py tests/test_phase2_rag.py -q
```

6. Run scoped `git diff --check`.

## Constraints

- Preserve pre-existing work and Task 1 changes.
- Only edit the listed production/test files plus `.superpowers/sdd/phase2-task-2-report.md`.
- Do not commit or stage.
- Report RED/GREEN evidence, files changed, and self-review findings.
