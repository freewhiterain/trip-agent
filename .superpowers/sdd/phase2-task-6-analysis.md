# Phase 2 Task 6: Frontend Integration Map

## Scope

The smallest frontend surface is the existing trip-tool state/render/SSE path in `1_zhixing.html`. The backend already produces structured worker results and durable governance events, but the current tool-result SSE stream exposes only `result`, `token`, and `done`; worker events need a transport bridge before the browser can handle them live.

## Stable worker rows

Render a fixed five-row collection, keyed by the `worker` value rather than arrival order:

1. `attractions`
2. `weather`
3. `transport`
4. `hotel`
5. `food`

The authoritative task list is generated in `create_research_plan` (`app/agents/planner.py:6-58`), and the registry confirms the same five keys in `create_default_registry` (`app/agents/workers/registry.py:42-58`). Keep rows in a frontend constant and merge event/result data into those rows so missing, delayed, or out-of-order workers still render as stable pending rows.

Recommended frontend integration points:

- `state` at `1_zhixing.html:1099-1106`: add per-tool worker status state, evidence, warnings, and an optional final result label.
- `tripToolDefaults` at `1_zhixing.html:1396-1415`: initialize the five rows with `pending` status and empty evidence/warnings.
- `renderTripTool` at `1_zhixing.html:1438-1554`: add the worker-status section, evidence-source section, warnings section, and final local-mock label without changing the existing three-step form.
- `applyTripToolResult` at `1_zhixing.html:1600-1622`: merge terminal tool results and any persisted draft fields into the same state before rendering.

The backend result contract is `WorkerResult` (`app/schemas/planning.py:110-120`): `worker`, `status`, `summary`, `evidence`, and `warnings`. Valid worker statuses are `completed`, `partial`, and `failed` (`app/schemas/planning.py:12-13`).

## Event handling

Add one event reducer, called from the existing `parseSseStream` callback at `1_zhixing.html:1699-1727` and `sendMessage` callback at `1_zhixing.html:1775-1787`.

- `worker_started`: read `event.payload` or legacy top-level fields; identify the row by `worker`, set `running`, retain its `task_id`.
- `worker_completed`: payload is the serialized `WorkerResult` from `worker_node` (`app/agents/supervisor.py:256-264`); merge the full row, including `status`, summary, evidence, and warnings.
- `partial`: treat as a worker result status, not necessarily a distinct SSE type; show the row as partial and expose its warnings.
- `failed`: treat as a worker result status and show a terminal failed row; the registry creates this result in `WorkerRegistry.run` (`app/agents/workers/registry.py:20-39`). Also handle `task_failed` from `run_travel_planning` (`app/agents/supervisor.py:316-350`) as a task-level failure without discarding already completed rows.
- `evidence_collected`: optional count-only update from `worker_node` (`app/agents/supervisor.py:261-264`); use it as a live count, then replace/expand with evidence from `worker_completed` or the final draft.

The current backend event production is in `emit` (`app/agents/supervisor.py:208-216`) and event persistence is in `TaskEventService.emit` (`app/governance/events.py:33-55`). `tool_result_stream` creates the planning task with that service (`app/api/v1/tools.py:224-249`), but only yields the final `result`, `token`, and `done` frames (`app/api/v1/tools.py:270-313`). Minimal transport work is therefore to publish/consume the persisted event stream between lines 240-249 and the final yields, or add an equivalent event SSE endpoint; frontend-only handling cannot receive worker events with the current stream.

## Final label, evidence, warnings

- Final local mock-data label: derive it from the draft/worker evidence metadata and/or warning state, and render it in `renderTripTool` after a terminal result. The label should explicitly say the plan uses local mock/sample data when sources are local or no live external tools were enabled. Do not infer this from `status === completed`; completed workers can still be partial in data quality.
- Evidence source display: each `Evidence` item has `content`, `source`, optional `source_url`, timestamps, confidence, and metadata (`app/schemas/planning.py:86-96`). Display `source` as the primary source label and link `source_url` when present; keep content available beside it.
- Warnings: merge and de-duplicate per-worker warnings plus final draft warnings. `assemble_draft` performs the backend de-duplication (`app/agents/supervisor.py:181-196`), while the frontend should still deduplicate during incremental event merges.
- Final payload: `tool_result_stream` stores `assistant_result`/`draft` in `extra_info` (`app/api/v1/tools.py:270-307`) and sends it in the `result` event (`app/api/v1/tools.py:311-313`), so the reducer should accept `payload.result` as either the assistant result or the nested draft and normalize both shapes.

## History reload compatibility

The existing reconstruction path is already the correct compatibility boundary:

- `switchConversation` fetches `/api/v1/chat/history/{id}` and calls `renderMessages` (`1_zhixing.html:1292-1323`).
- `renderMessages` resets `state.tripTools`, replays `extra_info.tool_call`, then replays `extra_info.tool_result` (`1_zhixing.html:1325-1366`).
- `restorePendingTool` re-renders non-completed, non-closed forms (`1_zhixing.html:1693-1697`).

Extend `renderMessages` to replay a persisted final draft/event summary if history contains it, but preserve the existing `tool_call` then `tool_result` ordering and call the same reducer used by live SSE. History currently persists the final `tool_result` and `assistant_result` in `Message.extra_info` (`app/api/v1/tools.py:299-307`); individual governance worker events are not written into chat history by the inspected code. Therefore history can restore final evidence/warnings only if they are present in `assistant_result`/`draft`, unless a separate event-history fetch is added.

## Test insertion points

The current test file only checks static frontend contracts (`tests/test_frontend_trip_form.py:11-24`, `26-42`, `44-52`). Add focused string-contract assertions there for the five worker keys, event names/statuses, source/warning rendering, local mock label, and the shared history/live reducer. No existing assertions need to move.

## Blockers

1. **Live worker events are not on the current browser stream.** `TaskEventService` persists events, but `tool_result_stream` does not yield them; Phase 2 needs that backend bridge or a separate SSE event endpoint.
2. **History has no inspected per-worker event persistence.** Final draft data can restore evidence/warnings only when included in `assistant_result`/`draft`; exact event-by-event replay requires an event-history API or persisted event snapshot in `Message.extra_info`.

