# Final Review Fix Report

Date: 2026-07-24

## Summary

Applied the three items from the final whole-branch code review on the layered
user memory feature: two "document, don't change behavior" Important findings
and one Minor logging fix. No runtime behavior was changed by Finding 1 or 2
(comment + docs only); Finding 3 adds a keyword argument to an existing log
call.

## Changes

### Finding 1 — `app/memory/trip_history.py`

Added an explanatory comment directly above the `period == "morning"` check
inside `_extract_visited_attractions`, documenting the implicit dependency on
`app/agents/supervisor.py`'s `build_itinerary`: that function always puts the
attraction name in the `morning` slot and the restaurant name in the
`evening` slot (`afternoon` is a generic placeholder, never a real title).
The comment explains why the function only reads `morning` slots and warns
that a future change to `build_itinerary`'s slot-content convention would
require a matching update here, or attractions get missed / restaurant names
get miscategorized as attractions.

No logic changed — same `if slot.get("period") == "morning" and
slot.get("title")` condition as before.

### Finding 2 — `docs/superpowers/specs/2026-07-24-layered-user-memory-design.md`

Added a paragraph immediately after the existing "偏好画像的 ADD-only 化"
section's description of the write-semantics change (section "### 2.
偏好画像的 ADD-only 化"). It explains, in the doc's existing Chinese
technical-writing style:

- The project has no migration tooling (no Alembic); schema changes take
  effect only via `Base.metadata.create_all` when `scripts/init_db.py` runs.
- `create_all` only creates missing tables/columns — it does not drop
  constraints from tables that already exist.
- On a database where `init_db()` already ran before this feature, the old
  `uq_user_preference_key` unique constraint on `user_preference` will still
  be present and must be dropped manually, via:
  `ALTER TABLE user_preference DROP CONSTRAINT uq_user_preference_key;`
- Otherwise, a second confirmation of the same preference key raises an
  unhandled `IntegrityError` (this write path has no degrade-on-failure
  wrapper, unlike the read path).

Only documentation was added; no code or migration script was created, per
the human's resolution of this finding.

### Finding 3 — `app/api/v1/planning.py`

In `create_planning_task`, changed the preference-defaults-lookup-failure
warning log call from:

```python
app_logger.warning(f"读取长期偏好失败，按无偏好处理: task_id={task_id} error={exc}")
```

to:

```python
app_logger.warning(f"读取长期偏好失败，按无偏好处理: task_id={task_id} error={exc}", exc_info=True)
```

so the stack trace is captured in logs for root-causing silently-degraded
preference lookups in production. `app_logger` is a standard
`logging.Logger` (see `app/utils/logger.py`), so `exc_info=True` is valid on
`.warning()`.

## Test Command and Output

```
python -m pytest tests/test_trip_history.py tests/test_task_creation_uses_preference_defaults.py -v
```

Result: **10 passed** in 14.16s. All tests in both files passed, including
the trip-history extraction tests and the preference-defaults degrade-on-
failure test — confirming none of the three changes altered behavior.

```
tests/test_trip_history.py::test_build_trip_history_record_extracts_destination_dates_and_attractions PASSED
tests/test_trip_history.py::test_build_trip_history_record_returns_none_when_requirement_missing PASSED
tests/test_trip_history.py::test_build_trip_history_record_returns_none_when_destination_missing PASSED
tests/test_trip_history.py::test_build_trip_history_record_tolerates_missing_itinerary_section PASSED
tests/test_trip_history.py::test_record_trip_history_from_itinerary_appends_to_repository PASSED
tests/test_trip_history.py::test_record_trip_history_from_itinerary_degrades_to_none_on_malformed_content PASSED
tests/test_task_creation_uses_preference_defaults.py::test_create_planning_task_fills_empty_fields_from_confirmed_preferences PASSED
tests/test_task_creation_uses_preference_defaults.py::test_create_planning_task_never_overrides_explicit_field PASSED
tests/test_task_creation_uses_preference_defaults.py::test_create_planning_task_degrades_to_no_defaults_when_preference_lookup_fails PASSED
tests/test_task_creation_uses_preference_defaults.py::test_decide_approval_wires_trip_history_repository_into_itinerary_apply PASSED

============================= 10 passed in 14.16s =============================
```

## Files Changed

- `D:\Desktop\project\Trip\app\memory\trip_history.py` (comment only)
- `D:\Desktop\project\Trip\docs\superpowers\specs\2026-07-24-layered-user-memory-design.md` (docs paragraph added)
- `D:\Desktop\project\Trip\app\api\v1\planning.py` (added `exc_info=True` to one warning call)

No other files were touched. The other Minor findings from the review
(duplicated warning branch, timestamp-assignment style inconsistency,
redundant `str()` wrap, missing `__init__.py` re-export, unvalidated budget
bypass) were intentionally left untouched per the human's decision.

## Commit

`23649e7` — "docs(memory): document morning-only attraction extraction and
manual constraint-drop step; log preference-lookup failures with traceback"

```
 3 files changed, 13 insertions(+), 1 deletion(-)
```

Only the three intended files were staged and committed. Pre-existing
unstaged modifications to `.superpowers/sdd/task-7-brief.md` and
`.superpowers/sdd/task-7-report.md` (present before this session started)
were left alone, as instructed.

## Concerns

None. All three changes are non-behavioral (comment, documentation, and a
logging keyword argument), the targeted test suite passes in full, and no
files outside the three specified were modified.
