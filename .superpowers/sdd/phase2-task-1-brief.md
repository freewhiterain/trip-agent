# Task 1: Add Chengdu Mock Knowledge Documents

## Files

- Create: `data/documents/attractions/chengdu.md`
- Create: `data/documents/weather/chengdu.md`
- Create: `data/documents/transport/chengdu.md`
- Create: `data/documents/accommodation/chengdu.md`
- Create: `data/documents/food/chengdu.md`
- Modify: `app/rag/document_loader.py`
- Test: `tests/test_phase2_mock_documents.py`

## Requirements

- `DocumentManager.load_all_documents()` keeps its existing public signature.
- Every new Chengdu fixture is clearly labeled `数据类型：模拟资料`, `适用城市：成都`, and `最后更新：开发测试数据`.
- Every loaded fixture has metadata `city="成都"`, `source_type="mock_markdown"`, and its Worker category.
- Directory categories map as follows: `attractions -> attractions`, `weather -> weather`, `transport -> transport`, `accommodation -> hotel`, `food -> food`.
- Preserve metadata and behavior for unrelated existing documents.
- Fixtures must not claim live prices, schedules, inventory, operating status, or current weather.

## TDD Cycle

1. Add a failing test proving all five Worker categories load with the required metadata.
2. Run `D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py::test_chengdu_mock_documents_have_worker_metadata -q` and record the expected failure.
3. Add the Markdown fixtures and minimal loader changes.
4. Run `D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py tests/test_phase2_rag.py -q` and record the passing result.
5. Run `git diff --check` for changed files.

## Constraints

- Do not call external APIs.
- Do not modify files outside the listed write scope except the report file.
- Preserve all pre-existing uncommitted changes.
- Do not commit or stage files.
- Write the implementation report to `.superpowers/sdd/phase2-task-1-report.md` with RED/GREEN evidence, files changed, and self-review findings.
