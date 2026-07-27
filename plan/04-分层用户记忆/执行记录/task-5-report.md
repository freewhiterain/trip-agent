# Task 5 Report: 接入 API 层

## What was implemented

Exactly the changes specified in `task-5-brief.md`, applied to `app/api/v1/planning.py`:

1. **Imports** — added `PostgresTripHistoryRepository` to the `app.governance.postgres` import block, added
   `from app.memory.defaults import apply_preference_defaults, resolve_preference_defaults`, and added
   `from app.utils.logger import app_logger`.

2. **`create_planning_task` (`POST /tasks`)** — before calling `run_travel_planning`, it now resolves the
   user's confirmed long-term preference defaults via `resolve_preference_defaults(str(user.id),
   PostgresPreferenceRepository())`, wrapped in a `try/except Exception` that logs a warning and falls back
   to `{}` on failure (e.g. DB down). The requirement is then rebuilt via
   `apply_preference_defaults(requirement, defaults)` — which only fills empty fields and never overrides
   explicit user input — before being passed downstream.

3. **`decide_approval` (`POST /approvals/{approval_id}/decision`)**, itinerary branch — `PostgresTripHistoryRepository()`
   is now passed as the third positional argument to `ItineraryGovernanceService(...)`, so `.apply()` has
   access to trip history when applying an approved/edited `itinerary.*` action.

Also added `tests/test_task_creation_uses_preference_defaults.py` with the 4 tests specified in the brief,
verbatim.

## TDD flow followed

1. Wrote the test file first (Step 1).
2. Ran it against the unmodified `planning.py` — confirmed 2 of 4 failed as predicted by the brief:
   - `test_create_planning_task_fills_empty_fields_from_confirmed_preferences` failed because
     `captured["requirement"].food_preferences` was still `[]` (no defaults merged yet).
   - `test_decide_approval_wires_trip_history_repository_into_itinerary_apply` failed with
     `AttributeError: <module 'app.api.v1.planning'> ... has no attribute 'PostgresTripHistoryRepository'`.
   (The other 2 tests already passed trivially since they didn't depend on the missing behavior.)
3. Applied the Step 3 edits exactly as given in the brief.
4. Reran the target test file — all 4 passed.
5. Ran the full regression set — all passed, no regressions.
6. Committed.

## Test commands run and output

```
python -m pytest tests/test_task_creation_uses_preference_defaults.py -v
```
```
tests/test_task_creation_uses_preference_defaults.py::test_create_planning_task_fills_empty_fields_from_confirmed_preferences PASSED [ 25%]
tests/test_task_creation_uses_preference_defaults.py::test_create_planning_task_never_overrides_explicit_field PASSED [ 50%]
tests/test_task_creation_uses_preference_defaults.py::test_create_planning_task_degrades_to_no_defaults_when_preference_lookup_fails PASSED [ 75%]
tests/test_task_creation_uses_preference_defaults.py::test_decide_approval_wires_trip_history_repository_into_itinerary_apply PASSED [100%]

============================= 4 passed in 20.76s ==============================
```

```
python -m pytest tests/test_task_creation_uses_preference_defaults.py tests/test_phase3_governance.py tests/test_phase4_api_and_sse.py -v
```
```
tests/test_task_creation_uses_preference_defaults.py::test_create_planning_task_fills_empty_fields_from_confirmed_preferences PASSED [  6%]
tests/test_task_creation_uses_preference_defaults.py::test_create_planning_task_never_overrides_explicit_field PASSED [ 12%]
tests/test_task_creation_uses_preference_defaults.py::test_create_planning_task_degrades_to_no_defaults_when_preference_lookup_fails PASSED [ 18%]
tests/test_task_creation_uses_preference_defaults.py::test_decide_approval_wires_trip_history_repository_into_itinerary_apply PASSED [ 25%]
tests/test_phase3_governance.py::test_memory_is_not_written_before_owner_approval PASSED [ 31%]
tests/test_phase3_governance.py::test_edit_and_delete_memory_require_separate_approvals PASSED [ 37%]
tests/test_phase3_governance.py::test_formal_itinerary_save_and_overwrite_are_approved_and_versioned PASSED [ 43%]
tests/test_phase3_governance.py::test_interrupt_workflow_pauses_and_resumes_with_same_thread PASSED [ 50%]
tests/test_phase3_governance.py::test_supervisor_persists_checkpoint_and_ordered_events PASSED [ 56%]
tests/test_phase3_governance.py::test_governance_tables_are_registered_for_database_initialization PASSED [ 62%]
tests/test_phase4_api_and_sse.py::test_stage4_routes_are_registered PASSED [ 68%]
tests/test_phase4_api_and_sse.py::test_sse_event_preserves_legacy_token_and_error_fields PASSED [ 75%]
tests/test_phase4_api_and_sse.py::test_rule_extractor_handles_common_complete_request PASSED [ 81%]
tests/test_phase4_api_and_sse.py::test_rendered_plan_is_legacy_readable_and_transaction_free PASSED [ 87%]
tests/test_phase4_api_and_sse.py::test_log_redaction_masks_common_credentials PASSED [ 93%]
tests/test_phase4_api_and_sse.py::test_frontend_buffers_partial_sse_frames PASSED [100%]

======================== 16 passed in 70.57s (0:01:10) ========================
```

## Files changed

- `app/api/v1/planning.py` (modified) — imports, `create_planning_task`, `decide_approval` itinerary branch.
- `tests/test_task_creation_uses_preference_defaults.py` (new) — 4 tests per brief.

Committed as `c0762d4` — "feat(memory): wire confirmed preference defaults and trip-history repository into planning API".

Note: several `.superpowers/sdd/task-*.md` files (including this one, which previously contained an unrelated
report for a different plan's "GraphCommunityService" task) show as modified in `git status` from before this
task started — pre-existing working-tree state unrelated to this task. They were deliberately left out of the
commit, which stages only the two files listed above.

## Self-review

- Implemented exactly what the brief specifies — no extra refactoring, no scope creep.
- Diff matches the brief's before/after snippets verbatim (verified via `git diff` prior to commit).
- Tests exercise real behavior: they assert on the actual `TravelRequirement` captured by the monkeypatched
  `run_travel_planning`, and on the `trip_history` argument captured by the monkeypatched
  `ItineraryGovernanceService.__init__` — not just "does it run without raising."
- No stray files or debug prints; only the two intended files are staged/committed.

## Concerns

None. All target and regression tests pass; the change is a small, mechanical wiring task as described.
