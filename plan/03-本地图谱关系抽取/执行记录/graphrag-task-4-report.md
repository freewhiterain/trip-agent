# Task 4: GraphKnowledgeService (Persistence + Query) — Report

## What I implemented

Created `app/agents/workers/graph_knowledge.py`, exactly as specified in the
task brief:

- `GraphKnowledgeService(session_factory=async_session_maker)` — follows the
  `app/governance/postgres.py` repository style (session-factory injection,
  `async with self.session_factory() as session, session.begin(): ...`).
  - `write_entities_and_relations(entities, relations) -> None`: upserts each
    `ExtractedEntity` via `insert(...).on_conflict_do_update(constraint="uq_knowledge_entity_identity", ...)`
    returning the entity id, builds a `(city, category, name) -> id` lookup,
    then upserts each `ResolvedRelation` via
    `insert(...).on_conflict_do_nothing(constraint="uq_knowledge_relation_identity")`,
    skipping any relation whose endpoints aren't in the lookup.
  - `search_related_entities(destination, category, query) -> list[Evidence]`:
    queries entities by `(city, category)`, their outgoing relations, and the
    relation targets, all inside one `try/except Exception` that returns `[]`
    on any failure (with a warning log). Builds one `Evidence` per relation
    with `content` like `"__attraction__ 位于 __area__"`, `source` set to the
    relation's `source_document`, `confidence` from the relation, and
    `metadata` carrying `source_type`, `category`, `relation_type`,
    `from_entity`, `to_entity`.
- `get_graph_knowledge_service() -> GraphKnowledgeService` — module-level
  singleton accessor (plain global, mirroring the shape of
  `local_knowledge.get_local_knowledge_service`, though that one uses
  `lru_cache` — the brief's given code uses a manual global instead, which is
  what I implemented verbatim).

Module's only public surface: `GraphKnowledgeService` and
`get_graph_knowledge_service` (the `_RELATION_LABELS` dict and
`_graph_knowledge_service` global are underscore-prefixed/private).

## What I tested

1. **Step 1/2** — Wrote `tests/test_graph_knowledge_service.py` (new file,
   no database) first, confirmed it failed with
   `ModuleNotFoundError: No module named 'app.agents.workers.graph_knowledge'`
   before the implementation existed.
2. **Step 3/4** — Implemented the service, reran the same test: **1 passed**.
3. **Step 5** — Appended the opt-in round-trip test
   (`test_write_and_search_round_trip_is_idempotent_and_category_scoped`) to
   the existing `tests/test_graph_knowledge_service_postgres.py` from Task 1,
   adding `from sqlalchemy import select`,
   `from app.agents.workers.graph_knowledge import GraphKnowledgeService`, and
   `from app.rag.graph_extraction import ExtractedEntity, ResolvedRelation` to
   that file's imports (placed at top of file rather than immediately before
   the new test, for consistency with the rest of the file's import block —
   no functional difference).
4. **Step 6** — Ran `tests/test_graph_knowledge_service_postgres.py`:
   **2 skipped** (both the pre-existing Task 1 test and the new round-trip
   test), reason `"requires RUN_POSTGRES_TESTS=1 and a reachable PostgreSQL
   database"`. I checked for a local reachable Postgres
   (`Test-NetConnection localhost -Port 5432` → `TcpTestSucceeded: False`) —
   none is available in this environment, so the skip is the correct,
   expected outcome per the task instructions; I did not set
   `RUN_POSTGRES_TESTS=1` and did not attempt to install/start a database.
5. Final sanity run: `test_graph_knowledge_service.py` +
   `test_graph_knowledge_service_postgres.py` +
   `test_graph_extraction.py` together → **11 passed, 2 skipped**.

## Files changed

- `D:\Desktop\project\Trip\app\agents\workers\graph_knowledge.py` (new)
- `D:\Desktop\project\Trip\tests\test_graph_knowledge_service.py` (new)
- `D:\Desktop\project\Trip\tests\test_graph_knowledge_service_postgres.py`
  (extended: added imports and the new round-trip test function)

Note: `.superpowers/sdd/progress.md` had a pre-existing uncommitted
modification in the working tree at the start of this session (from a prior
task's process, not authored by me in this session). I left it untouched and
did not include it in my commit, since it's outside this task's file scope.

## Self-review findings

Checked the three points called out in the task brief:

1. **`search_related_entities` exception handling**: the `try:` opens
   immediately before `async with self.session_factory() as session:` and the
   matching `except Exception as exc:` closes after all three
   `session.execute(...)` calls (entities, relations, targets) and their
   early-return branches — the entire database interaction, including the
   `session_factory()` call itself, is inside the try block. Any exception
   from session creation, connection, or any of the three queries is caught
   and degrades to `return []` with a warning log. Verified by inspection of
   the try/except boundary, not just by the passing unit test (which only
   exercises the `session_factory()`-raises path).
2. **`entity_ids` key shape**: built as
   `entity_ids[(item.city, item.category, item.name)] = entity_id` from
   `ExtractedEntity` fields, and looked up as
   `entity_ids.get((relation.from_city, relation.from_category, relation.from_name))`
   / `(relation.to_city, relation.to_category, relation.to_name)` from
   `ResolvedRelation` fields — the 3-tuple shape and field names line up
   exactly.
3. **Public surface**: only `GraphKnowledgeService` and
   `get_graph_knowledge_service` are unprefixed module-level names.

No issues found. No deviations from the brief's given code.

## Concerns

None. The opt-in Postgres round-trip test could not be executed against a
real database in this environment (no reachable local Postgres), so its
correctness rests on the brief's specification and the passing unit test of
the error path plus code inspection — this matches the task's explicit
allowance that a skip is the expected, non-blocking outcome here.
