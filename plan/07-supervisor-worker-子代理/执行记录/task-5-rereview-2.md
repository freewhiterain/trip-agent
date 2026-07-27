# Task 5 Re-review 2

## Scope

Reviewed only the remaining Medium finding from `task-5-rereview.md`, using `task-5-report.md` and the fix diff `review-be98b86..f377fdd.diff`.

Finding under review: failed `SubagentResponse` summaries must not inject arbitrary factual text into downstream synthesis.

## Verdict

Status: ADDRESSED.

The fix changes `_governed_summary()` so any `SubagentResponse` with `status == "failed"` returns the fixed non-factual summary `"Domain subagent execution failed."` instead of preserving `response.summary` (`app/agents/supervisor.py:179`, `app/agents/supervisor.py:181`).

This closes the prior leak path because `_subagent_response_to_worker_result()` assigns `WorkerResult.summary` only from `_governed_summary()` (`app/agents/supervisor.py:166`, `app/agents/supervisor.py:171`), and the LLM evidence digest uses `result.summary` for downstream synthesis (`app/agents/supervisor.py:299`). A schema-valid failed response can no longer place arbitrary factual raw summary text into that digest.

The added regression covers the exact failure mode by constructing a failed `SubagentResponse` whose raw summary says `"Panda Base tickets are sold out today."`, then asserting the resulting worker summary is only `"Domain subagent execution failed."` and does not contain the raw factual text (`tests/test_supervisor_subagent_merge.py:71`, `tests/test_supervisor_subagent_merge.py:82`, `tests/test_supervisor_subagent_merge.py:83`).

Warnings remain preserved as diagnostics (`tests/test_supervisor_subagent_merge.py:84`), but the synthesis digest path reviewed here does not include `result.warnings`; it includes summaries, option names, and evidence content only. That is outside the remaining Medium summary-injection finding.

## New issues introduced by this fix diff

No new Critical, Important, Medium, or Low issues found in `review-be98b86..f377fdd.diff`.

## Verification

Focused regression:

```text
.venv\Scripts\python.exe -m pytest tests\test_supervisor_subagent_merge.py::test_failed_worker_result_summary_uses_fixed_non_factual_text -q
1 passed, 1 warning in 0.52s
```

Direct probe:

```text
_subagent_response_to_worker_result(
    SubagentResponse(
        task_id="task-a",
        worker="attractions",
        status="failed",
        summary="Unsupported factual summary: Panda Base tickets are sold out today.",
    )
).summary
=> Domain subagent execution failed.
```
