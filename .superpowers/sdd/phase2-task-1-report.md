# Phase 2 Task 1 Report

## RED evidence

Command:

```powershell
D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py::test_chengdu_mock_documents_have_worker_metadata -q
```

Result: `1 failed`. The focused test failed with `FileNotFoundError` for
`data/documents/attractions/chengdu.md`, demonstrating that the required
Chengdu mock fixtures were absent before implementation.

An initial version of the test loaded the full existing document directory and
was blocked by a pre-existing UTF-8 document being opened with the Windows GBK
default, followed by unavailable optional `chardet` detection. The test was
then isolated to the Task 1 fixture contract before production code changed.

## GREEN evidence

Command:

```powershell
D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py tests/test_phase2_rag.py -q
```

Result: `6 passed, 1 warning in 6.28s`. The warning is an upstream
`pkg_resources` deprecation warning from `jieba`.

## Files changed

- `app/rag/document_loader.py`
- `tests/test_phase2_mock_documents.py`
- `data/documents/attractions/chengdu.md`
- `data/documents/weather/chengdu.md`
- `data/documents/transport/chengdu.md`
- `data/documents/accommodation/chengdu.md`
- `data/documents/food/chengdu.md`
- `.superpowers/sdd/phase2-task-1-report.md`

## Self-review

- Confirmed the new fixtures contain the three required labels and no live
  prices, schedules, inventory, operating status, or current weather claims.
- Confirmed only `chengdu.md` fixtures in the specified worker directories are
  retagged as `mock_markdown`; unrelated documents retain their existing
  metadata behavior.
- Confirmed `load_all_documents()` retains its public signature and includes
  all five worker categories.
- Ran a scoped `git diff --check` and trailing-whitespace scan. Git emitted
  only its existing LF-to-CRLF conversion warning; no whitespace errors were
  reported.

## Review Fix: Encoding And Fixture Coverage

### Changes

- The loader now opens UTF-8 Chengdu fixtures reliably while retaining
  `autodetect_encoding=True` as the fallback for unrelated and future
  non-UTF-8 documents.
- The metadata test now calls `DocumentManager().load_all_documents()` against
  the repository fixture files and asserts each loaded document's decoded
  labels and worker metadata.
- Added a regression test that copies the actual repository food fixture bytes
  beside a Latin-1 unrelated document and verifies encoding detection fallback.

### RED evidence

Command:

```powershell
D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py::test_loader_falls_back_to_detected_encoding_for_unrelated_documents -q
```

Exact result summary:

```text
FAILED tests/test_phase2_mock_documents.py::test_loader_falls_back_to_detected_encoding_for_unrelated_documents
1 failed in 4.27s
```

The failure was `RuntimeError: Error loading ...\\food\\legacy.md`, caused by
`UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9`, proving the
UTF-8-only loader did not retain fallback behavior.

### GREEN evidence

Focused command:

```powershell
D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py::test_loader_falls_back_to_detected_encoding_for_unrelated_documents -q
```

Exact output:

```text
.                                                                        [100%]
1 passed in 6.35s
```

Requested suite command:

```powershell
D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest tests/test_phase2_mock_documents.py tests/test_phase2_rag.py -q
```

Exact output summary:

```text
.......                                                                  [100%]
7 passed, 1 warning in 4.27s
```

The warning remains the upstream `jieba` `pkg_resources` deprecation warning.
