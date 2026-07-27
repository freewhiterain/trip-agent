# Task 3 Report: 行程历史记录（Layer 2b）

## What was implemented

Implemented exactly what the task brief specified, following TDD in the given order:

1. **`tests/test_trip_history.py`** (new) — 6 tests covering `build_trip_history_record`
   (destination/dates/attractions extraction, `None` on missing `requirement`, `None` on
   missing `destination`, tolerant of missing `itinerary` section) and
   `record_trip_history_from_itinerary` (appends to repository on success, degrades to `None`
   without raising or writing on malformed content).

2. **`app/schemas/governance.py`** — added `date` to the top-level `datetime` import, and a new
   `TripHistoryRecord` Pydantic model (`id`, `user_id`, `destination`, `start_date`, `end_date`,
   `visited_attractions: list[str]`, `source_itinerary_id`, `confirmed_at`) placed between
   `PreferenceRecord` and `ApprovalDecisionRequest`.

3. **`app/models/governance.py`** — added `date` and `Date` to the imports, and a new `TripHistory`
   ORM class (mirrors `TripHistoryRecord`'s shape; `user_id` FKs to `user.id` with cascade delete,
   `source_itinerary_id` FKs to `saveditinerary.id` with cascade delete, `visited_attractions` is
   a `JSON` column defaulting to `list`).

4. **`app/models/__init__.py`** — registered `TripHistory` in the import from
   `app.models.governance` and in `__all__` (alphabetically after `TripDraft`).

5. **`app/memory/trip_history.py`** (new) — `TripHistoryRepository` Protocol,
   `InMemoryTripHistoryRepository` (deep-copies on append/list, filters by `user_id`),
   `_extract_visited_attractions` (pulls `title` from every `morning`-period slot across all
   itinerary days), `build_trip_history_record` (pure function; returns `None` — never raises —
   when `requirement` is missing/not a dict, `destination`/`departure_date` are missing, `days`
   isn't a positive int, or `departure_date` isn't a valid ISO date; computes `end_date` as
   `start_date + (days - 1)` days), and `record_trip_history_from_itinerary` (async wrapper that
   catches all exceptions, logs a warning via `app_logger`, and returns `None` rather than
   propagating — since this is a secondary side effect of itinerary-save, not the primary
   action).

6. **`app/governance/postgres.py`** — added `TripHistory`/`TripHistoryRecord` imports, and a new
   `PostgresTripHistoryRepository` class with `append` (inserts a `TripHistory` row inside a
   transaction, returns the round-tripped `TripHistoryRecord`) and `list` (selects by `user_id`,
   ordered by `confirmed_at`).

7. **`tests/test_preference_and_trip_history_postgres.py`** (new) — opt-in integration test file
   gated by `pytest.mark.external` + `skipif(os.getenv("RUN_POSTGRES_TESTS") != "1")`, per this
   project's existing convention. Contains the preference-repository round-trip test (pre-existing
   convention, included per brief Step 9's exact code) plus a new
   `test_postgres_trip_history_append_and_list_round_trip` that creates a `User`, appends a
   `TripHistoryRecord` via `PostgresTripHistoryRepository`, lists it back, and asserts the
   destination/attractions round-trip correctly; cleans up both rows in a `finally` block.

Nothing was wired into the itinerary-save flow — per the task boundary, that's Task 4's job.

## Test commands run and output

**Step 2 (RED, before any production code):**
```
python -m pytest tests/test_trip_history.py -v
```
Result: `ModuleNotFoundError: No module named 'app.memory.trip_history'` (1 error during
collection) — matches brief's expected failure exactly.

**Step 7 (GREEN, after Steps 3-6):**
```
python -m pytest tests/test_trip_history.py -v
```
Result: `6 passed in 0.76s`.

**Postgres opt-in test, default mode (no `RUN_POSTGRES_TESTS` set):**
```
python -m pytest tests/test_trip_history.py tests/test_preference_and_trip_history_postgres.py -v
```
Result: `6 passed, 2 skipped in 4.02s` — the two Postgres tests are auto-skipped as designed.

**Postgres opt-in test, attempted with `RUN_POSTGRES_TESTS=1`:**
```
RUN_POSTGRES_TESTS=1 python -m pytest tests/test_preference_and_trip_history_postgres.py -v
```
Result: `2 failed in 18.29s` — both fail with
`OSError: Multiple exceptions: [Errno 10061] Connect call failed ('::1', 15432, 0, 0), [Errno 10061]
Connect call failed ('127.0.0.1', 15432)`. There is no local PostgreSQL reachable in this
environment (connection refused on port 15432), so setting the env var turns the skip into a
connection-refused failure rather than a passing run — this is expected per the task
instructions ("if it skips because there's no local PostgreSQL reachable, that's expected and
fine"); with the env var unset (the default, and the state actually left in the repo/CI), both
tests skip cleanly.

**Regression check (governance + preference + trip-history tests, avoiding the
RAG/Ollama-dependent suite which appears to hang/take very long in this environment):**
```
python -m pytest tests/test_phase3_governance.py tests/test_preference_and_trip_history_postgres.py \
  tests/test_preference_append_only.py tests/test_preference_defaults.py tests/test_trip_history.py -v
```
Result: `23 passed, 2 skipped in 178.21s (0:02:58)` — includes
`test_governance_tables_are_registered_for_database_initialization`, confirming the new
`TripHistory` model registration doesn't break DB table initialization discovery.

## Files changed

- `app/models/governance.py` (modified: new `TripHistory` ORM class + import additions)
- `app/models/__init__.py` (modified: register `TripHistory`)
- `app/schemas/governance.py` (modified: new `TripHistoryRecord` + `date` import)
- `app/governance/postgres.py` (modified: new `PostgresTripHistoryRepository` + import additions)
- `app/memory/trip_history.py` (new)
- `tests/test_trip_history.py` (new)
- `tests/test_preference_and_trip_history_postgres.py` (new)

Commit: `0bab8e6` — `feat(memory): add trip-history record model, repository, and extraction`

## Self-review

- All 10 steps in the brief implemented verbatim; production code matches the brief's code
  blocks exactly (no deviations were needed — no attribute-name mismatches, no missing
  dependencies).
- Tests exercise real behavior, not trivial mocks: `build_trip_history_record` tests assert on
  exact extracted `destination`/`start_date`/`end_date`/`visited_attractions` values, and on
  `None` returns for three distinct malformed-input shapes; the async tests assert on repository
  state after the call (`repository.list(...)`), not just on the return value.
- Confirmed only the 7 intended files are staged in the commit (`git status --short` before
  committing) — the pre-existing unstaged modifications to `.superpowers/sdd/*.md` files (present
  in the working tree since before this task started, per the initial git status) were correctly
  left untouched and unstaged.
- No stray files or debug prints; `app/memory/trip_history.py` matches the brief's code block
  exactly.
- `TripHistory.user_id` and `TripHistory.source_itinerary_id` both cascade-delete as specified,
  consistent with the existing `SavedItinerary`/`UserPreference` FK patterns in the same file.

## Concerns

- The opt-in Postgres test could not be verified against a real database in this environment (no
  local PostgreSQL running on port 15432) — this matches the task instructions' documented
  expectation and is not a blocker. The in-memory-path logic (`build_trip_history_record`,
  `record_trip_history_from_itinerary`, `InMemoryTripHistoryRepository`) is fully covered by
  `tests/test_trip_history.py`, and `PostgresTripHistoryRepository`'s code is a direct structural
  mirror of the already-tested `PostgresPreferenceRepository`/`PostgresItineraryRepository`
  patterns in the same file.
- I did not run the full `tests/` suite — a `python -m pytest tests/ -q` attempt ran past 250+
  seconds without completing (likely blocked on a RAG/Ollama-dependent external test elsewhere in
  the suite, unrelated to this task) and was stopped. Instead I ran a targeted regression pass
  over the governance/preference/trip-history test files that touch the models and schemas this
  task modified, which all passed cleanly.
