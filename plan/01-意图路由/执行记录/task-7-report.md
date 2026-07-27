# Task 7 Report: Rename Destination Research To Attractions

Status: DONE

## Scope

- Renamed the planning responsibility from `destination` to `attractions`.
- Renamed `DestinationWorker` and `app/agents/workers/destination.py` to `AttractionsWorker` and `app/agents/workers/attractions.py`.
- Updated planning task types, worker registry, supervisor itinerary lookup, and read-only worker tool name.
- Kept the existing `load_destination_evidence` source unchanged; Worker data-source design remains deferred.

## TDD Evidence

- RED: `.venv\\Scripts\\python.exe -m pytest tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py -q`
  - Result: 6 failed, 2 passed.
  - Expected failures showed the old `destination` task type, WorkerResult contract, and worker tool name.
- GREEN: `.venv\\Scripts\\python.exe -m pytest tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py tests/test_phase6_coordinator.py -q`
  - Result: 17 passed.

## Verification

- `git diff --check` for Task 7 implementation and test files passed with no whitespace errors.
- Test warnings are pre-existing dependency deprecations and a denied `.pytest_cache` write; they do not affect test results.

## Git

- No branch created and no commit made, per instruction.

## P2 Follow-up: Coordinator Responsibility Name

- Updated `SLICE_KEYWORDS` in `app/agents/coordinator.py` so sightseeing keywords route to `attractions`, not `destination`.
- Updated the `tests/test_phase6_coordinator.py` registry fixture and added an attractions keyword-routing assertion.
- RED verification: `.venv\\Scripts\\python.exe -m pytest tests/test_phase6_coordinator.py -q` returned `1 failed, 9 passed`; the failure showed the old `destination` keyword mapping violated the renamed `TaskType` contract.
- The production mapping is now corrected. Per interruption request, the post-fix focused test run was not started; re-run the Task 7 focused suite before treating this follow-up as fully verified.
