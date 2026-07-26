# Trip Agent Completion Design

## Goal

Turn the current travel-planning prototype into a coherent Supervisor-Worker system with one production worker runtime, explicit Deep Search behavior, governed evidence, a durable editable trip draft, and a demonstrable chat workflow.

## Scope

This work is split into independently testable phases:

1. Unify production execution around the Subagent Registry and make Agent-as-Tool reuse that runtime.
2. Make Deep Search explicit for research requests that require it, while preserving per-worker tool policy.
3. Stream research events and make Evidence Arbiter select usable evidence, surface unresolved conflicts, and block unsafe conclusions.
4. Connect the Trip Draft repository to planning and chat, including task ownership, status, versioning, and local edits.
5. Replace the fixed itinerary template with constraint-aware scheduling and transparent budget calculation.
6. Complete the frontend event protocol, startup configuration, security defaults, external integration tests, and documentation.

## Architecture

The request path is:

```text
Chat/Main Agent -> Supervisor -> shared Subagent Registry -> domain Subagent
                                      |                       |
                                      |                       +-> policy-selected tools
                                      |                       +-> optional explicit Deep Search
                                      |
                                      +-> Evidence Arbiter -> Scheduler -> Trip Draft
```

The Supervisor and Agent-as-Tool adapter use the same registry factory and provider configuration. A domain subagent returns a structured response containing claims, candidates, evidence IDs, warnings, and status. The Supervisor does not consume free-form summaries as facts.

Deep Search is a bounded capability, not an always-on worker. An explicit deep-research request sets `research_mode=deep`; the subagent must run the bounded search loop when its policy permits it. Provider fallback remains available for normal research. Weather and transport keep their policy restrictions and do not silently claim Deep Search was used.

Evidence governance happens before scheduling. Evidence is normalized, deduplicated, ranked by provider metadata, and mapped to claims. An unresolved conflict marks dependent facts as unavailable for scheduling. The final draft carries evidence references and warnings instead of silently selecting an unsupported fact.

Trip Draft is the durable workspace boundary. Planning creates or updates a draft version; chat reads the current draft and applies typed edits; approval and final itinerary save validate ownership and task/conversation linkage.

## Data Contracts

- `ResearchTask` carries the worker type, query, and research mode.
- `SubagentResponse` carries `status`, `claims`, `candidates`, `evidence`, `research_report`, and warnings.
- Every claim and candidate consumed by the Supervisor must reference evidence IDs that survived governance.
- `ReviewedResearch` carries conflicts and a set of facts safe for downstream scheduling.
- `TripDraftRecord` is keyed by `(user_id, conversation_id)` and increments its version on every accepted update.
- Task status is one of `pending`, `running`, `completed`, `degraded`, or `failed`.

## Error Handling

Provider failures are isolated per worker. A failed optional provider can produce a degraded research response, but it cannot produce invented facts. A failed required stage produces a failed task and a user-visible warning. Unresolved evidence conflicts prevent dependent itinerary items from being scheduled and are shown in the draft.

SSE persistence is ordered so that tool invocation, tool result, and assistant output are durable before the corresponding terminal event is emitted. Reconnecting clients can recover the current draft and task events from storage.

## Non-Goals

- Booking, payment, cancellation, or external message delivery.
- Unlimited autonomous search loops.
- Supporting every destination before the knowledge corpus and provider adapters exist.
- Removing the user's existing `.superpowers/sdd` changes.

## Acceptance Criteria

- The default production planning path uses the Subagent Registry; Legacy Worker use is explicit compatibility mode only.
- Supervisor and Agent-as-Tool share the same configured registry and provider instances.
- An explicit deep-research request produces Deep Search events or a clear policy/unavailable warning.
- An unresolved evidence conflict cannot become a scheduled factual itinerary item.
- Planning task status, ownership, draft version, and conversation linkage are validated.
- Chat can read the current draft and apply at least typed day/activity edits without rebuilding unrelated research.
- The schedule includes time, location, travel, opening-window, weather, and budget constraints when data exists; missing data is visible.
- Frontend renders Agent Tool and Deep Search events and the final evidence/warning state.
- Unit, integration, and external-provider tests cover success, degraded, failure, conflict, and recovery paths.

