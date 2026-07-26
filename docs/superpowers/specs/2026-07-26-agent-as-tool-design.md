# Agent-as-Tool Design

## Goal

Expose the existing domain subagents as explicit read-only tools that the Main Agent can invoke for focused research questions, while preserving Supervisor-Worker as the workflow for complete trip planning.

## Non-Goals

- Do not create a second set of domain subagents.
- Do not replace the Supervisor for complete itinerary planning.
- Do not let Agent Tools modify itineraries, preferences, memories, or external systems.
- Do not turn Evidence Governance into another free-form agent.
- Do not add unbounded multi-round autonomy to the Main Agent tool call in the first version.

## Architecture

```text
Conversation/Main Agent
    -> AgentToolExecutor interface
        -> AgentToolRegistry
            -> SubagentAdapter
                -> existing DomainSubagent
                    -> Local RAG / MCP / Deep Search
                        -> grounded SubagentResponse
```

The Supervisor remains an independent entry point:

```text
Supervisor
    -> existing SubagentRegistry
        -> existing DomainSubagent workers in parallel
```

Both paths reuse the same domain subagent implementations. Agent-as-Tool adds an invocation adapter; it does not add another reasoning layer or duplicate worker classes.

## Tool Contract

The first version exposes five explicit, read-only tool names:

- `research_attractions`
- `research_weather`
- `research_transport`
- `research_hotel`
- `research_food`

Each tool receives a lightweight `ResearchContext` containing:

- `destination`
- `query`
- optional `departure_date`
- optional `days`

The tool adapter converts this context into the existing `ResearchTask` contract and invokes the selected domain subagent through an injected executor. The Main Agent depends on the tool contract and tool names, not on concrete subagent classes.

The result contains:

- `tool_name`
- `worker`
- `status`
- grounded answer text
- evidence count
- stable warning codes

Raw provider exceptions and hidden provider payloads must not cross the tool boundary.

## Routing Rules

The Main Agent keeps the current routing behavior for ordinary questions:

```text
ordinary open question -> Local RAG
explicit deep/current research request -> one Agent Tool
complete trip planning -> trip form -> Supervisor
```

Examples:

- `成都有什么好玩的` remains a normal local RAG question.
- `深入研究成都适合亲子游的景点` invokes `research_attractions`.
- `帮我查一下成都最近的天气` invokes `research_weather`.
- `帮我规划成都三天行程` opens the existing form and later invokes Supervisor.

If the destination or tool target is ambiguous, the Main Agent does not guess a worker; it keeps the existing direct/RAG behavior or asks for clarification.

## SSE and Persistence

Agent-as-Tool is a read-only, per-turn operation and does not reuse the trip-form exactly-once persistence workflow.

The chat stream exposes:

```text
tool_call -> tool_result -> token -> done
```

The assistant message may persist the selected tool and result metadata for traceability, but the tool itself must not perform durable business mutations.

## Error Handling

- Unknown tool name: reject through the tool registry with a stable validation code.
- Missing or invalid context: return a validation error without invoking a worker.
- Subagent failure: return `failed` or `unavailable` with stable warning codes.
- Missing evidence: return a partial or unavailable answer; never invent facts.
- Tool execution exceptions: sanitize the public result and preserve the existing chat error contract.

## Testing Strategy

The implementation must add focused tests for:

1. Tool registry dispatches the selected name to the matching existing subagent.
2. Main Agent routes explicit deep-research requests and preserves legacy routing for ordinary questions.
3. Chat SSE emits the documented tool call, result, answer, and completion events.
4. Invalid tool names and subagent failures produce stable sanitized results.
5. Existing Supervisor, trip-form, and local RAG tests remain green.

## Acceptance Criteria

- Main Agent can invoke one existing domain subagent as a named tool.
- No duplicate domain subagent implementation is introduced.
- Supervisor still invokes the same workers independently for full planning.
- Agent Tool output is evidence-grounded and has stable status/warnings.
- SSE makes the Agent-as-Tool call visible without exposing secrets or raw provider data.
- Existing planning and RAG behavior remains compatible.
