# Task 3: Evidence-Bound Worker Agent Analysis

## Scope

Create `app/agents/workers/rag_analysis.py`, modify `app/schemas/planning.py`, and extend `tests/test_phase2_rag_workers.py`.

## Contract

Add `WorkerAnalysis` with `summary`, `options`, `warnings`, and `used_mock_data`, plus `async analyze_worker_evidence(worker, task, requirement, evidence, llm=None) -> WorkerAnalysis`.

Add `unavailable` to `WorkerStatus` and `is_mock: bool = False` to `WorkerResult`.

The analysis prompt may use only supplied `Evidence`; it must not invent prices, schedules, operating status, inventory, or weather facts. Do not expose hidden chain-of-thought. If there is no LLM or the structured call fails, return an evidence-only deterministic summary. With no evidence, return no options and a warning.

## TDD

1. Add failing tests for structured evidence-backed analysis, no-evidence fallback, and the new result/status fields.
2. Run the focused tests and capture RED.
3. Implement the Pydantic analysis model, structured-output path, and deterministic fallback.
4. Run the focused Phase 2 RAG Worker tests and relevant planning/supervisor contract tests.
5. Run `git diff --check`.

## Constraints

- No external network calls in tests.
- Preserve existing WorkerResult fields and serialized compatibility.
- Only edit the listed files plus `.superpowers/sdd/phase2-task-3-report.md`.
- Do not stage or commit.
- Report exact RED/GREEN output and self-review findings.
