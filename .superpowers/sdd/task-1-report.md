# Task 1: 偏好存储改为 ADD-only — Implementation Report

## Status
DONE

## Summary
Successfully implemented append-only semantics for user preference storage, replacing the previous overwrite-by-key behavior. This preserves preference history for later use by downstream tasks.

## What Was Implemented

### 1. New File: `tests/test_preference_append_only.py`
Created comprehensive test suite with 4 tests verifying append-only behavior:
- `test_upsert_appends_new_record_instead_of_overwriting`: Confirms multiple writes to the same key create separate records
- `test_delete_removes_all_historical_records_for_key`: Verifies delete removes all history for a key
- `test_delete_only_affects_matching_user_and_key`: Confirms delete isolation between users/keys
- `test_delete_returns_false_when_nothing_matches`: Verifies false return on missing delete

### 2. Modified: `app/memory/service.py`
Updated `InMemoryPreferenceRepository` with:
- Changed internal storage from `dict[tuple[str, str], PreferenceRecord]` to `list[PreferenceRecord]`
- **`upsert()` method:** Now appends records instead of overwriting; sets both `confirmed_at` and `updated_at` to current time
- **`delete()` method:** Filters out all records matching user_id and key
- **`list()` method:** Returns all historical records for a user in insertion order

### 3. Modified: `app/models/governance.py`
Enhanced `UserPreference` model with:
- Removed `UniqueConstraint("user_id", "key")` to allow multiple records per key
- Added `index=True` to `key` column for efficient historical lookups
- Added `index=True` to `confirmed_at` column for efficient sorting by insertion time
- Kept `UniqueConstraint` import as it's used by `TaskEvent` and `SavedItinerary`

### 4. Modified: `app/governance/postgres.py`
Updated `PostgresPreferenceRepository` with:
- Removed `insert` import from `sqlalchemy.dialects.postgresql` (no longer needed for conflict handling)
- **`upsert()` method:** Changed from INSERT...ON CONFLICT to simple INSERT; creates new entity each time
- **`delete()` method:** Changed to find and delete all matching records (plural) instead of just one
- **`list()` method:** Updated order_by to `(UserPreference.key, UserPreference.confirmed_at)` for consistent ordering

## Test Results

### RED Phase (Before Implementation)
```
Command: python -m pytest tests/test_preference_append_only.py -v

Results:
tests/test_preference_append_only.py::test_upsert_appends_new_record_instead_of_overwriting FAILED
tests/test_preference_append_only.py::test_delete_removes_all_historical_records_for_key PASSED
tests/test_preference_append_only.py::test_delete_only_affects_matching_user_and_key PASSED
tests/test_preference_append_only.py::test_delete_returns_false_when_nothing_matches PASSED

1 failed, 3 passed in 1.80s
```

**Failure:** `AssertionError: assert 1 == 2` — len(records) was 1 instead of 2 (overwrite behavior, expected)

### GREEN Phase (After Implementation)
```
Command: python -m pytest tests/test_preference_append_only.py -v

Results:
tests/test_preference_append_only.py::test_upsert_appends_new_record_instead_of_overwriting PASSED
tests/test_preference_append_only.py::test_delete_removes_all_historical_records_for_key PASSED
tests/test_preference_append_only.py::test_delete_only_affects_matching_user_and_key PASSED
tests/test_preference_append_only.py::test_delete_returns_false_when_nothing_matches PASSED

4 passed in 0.11s
```

### Regression Tests (Phase 3 Governance)
```
Command: python -m pytest tests/test_preference_append_only.py tests/test_phase3_governance.py -v

Results:
tests/test_preference_append_only.py (4 tests) — PASSED
tests/test_phase3_governance.py (6 tests) — PASSED

10 passed in 168.94s
```

**Conclusion:** All new append-only tests pass; all existing governance tests pass without modification (append-only behavior compatible with existing flows that write one record per key).

## Files Changed

1. **Created:** `tests/test_preference_append_only.py` (45 lines)
   - Complete test suite for append-only preference behavior
   - 4 test functions covering upsert appending, delete isolation, and missing key handling

2. **Modified:** `app/memory/service.py` (lines 19-37)
   - Replaced dict-based storage with list-based for append-only semantics
   - Updated upsert, delete, and list methods to handle multiple records per key

3. **Modified:** `app/models/governance.py` (lines 38-47)
   - Removed `__table_args__` with UniqueConstraint
   - Added indexes to `key` and `confirmed_at` for efficient querying
   - Kept UniqueConstraint import (used elsewhere)

4. **Modified:** `app/governance/postgres.py` (lines 8, 88-137)
   - Removed sqlalchemy.dialects.postgresql.insert import
   - Updated all three repository methods for append-only behavior

## Self-Review Findings

### Code Quality Checkpoints
- ✓ Implementation matches brief specification exactly (no deviations)
- ✓ TDD approach: wrote failing test, confirmed failure, implemented, confirmed passing
- ✓ Tests verify behavior, not just exercise code (record count, history isolation, deletion)
- ✓ No stray files, debug code, or unnecessary changes
- ✓ All 4 new tests pass (100%)
- ✓ All 6 existing governance tests pass (no regressions)
- ✓ Append-only semantics implemented in both in-memory and PostgreSQL repositories
- ✓ Indexes added for efficient historical queries (key and confirmed_at)

### Verification Steps Completed
1. RED phase: Created test, confirmed 1 failure (append semantics missing)
2. Implementation: Modified both repositories and model
3. GREEN phase: 4 new tests passed
4. Regression suite: 6 existing tests passed
5. Git commit: Successful with exact message

## Commit Details

**SHA:** `ef8d1c3`
**Message:** `feat(memory): make preference writes append-only instead of overwrite-by-key`
**Files Changed:** 
- app/memory/service.py (modified)
- app/models/governance.py (modified)
- app/governance/postgres.py (modified)
- tests/test_preference_append_only.py (created)

## No Issues or Concerns

All work completed as specified. Code is production-ready for downstream tasks (Task 2 will build a resolver to read latest value per key on top of this append-only foundation).
