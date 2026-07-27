# Task 4 Report: Domain Subagent Runners

## Status

Implemented Task 4 in the main checkout.

## Scope

Created the domain subagent layer under `app/agents/subagents`:

- `base.py`
- `attractions.py`
- `weather.py`
- `transport.py`
- `hotel.py`
- `food.py`
- `registry.py`

Added focused tests in:

- `tests/test_domain_subagents.py`

Updated package exports in:

- `app/agents/subagents/__init__.py`

## Behavior Implemented

- Added `DomainSubagent.run(task, requirement) -> SubagentResponse`.
- Added five domain subagents:
  - `AttractionsSubagent`
  - `WeatherSubagent`
  - `TransportSubagent`
  - `HotelSubagent`
  - `FoodSubagent`
- Added `SubagentRegistry.run(task, requirement) -> SubagentResponse`.
- Preserved existing `app.agents.workers` registry/callers; no legacy worker registry changes.
- Reused existing contracts:
  - `ResearchTask`
  - `TravelRequirement`
  - `SubagentResponse`
  - `EvidenceBoundCandidate`
  - `Claim`
  - `ResearchReport`
  - `ToolPolicy`
  - normalized tool/provider outputs
  - bounded `run_deep_search`
- Enforced provider order and fallback:
  - weather: `weather_mcp -> weather_fallback_api`
  - transport: `transport_mcp -> search_mcp`
  - attractions: `local_rag -> search_mcp -> Deep Search only if needed`
  - hotel: `local_rag -> hotel_mcp -> search_mcp -> Deep Search only if needed`
  - food: `local_rag -> search_mcp -> Deep Search only if needed`
- Weather and transport do not invoke Deep Search because their `ToolPolicy` disallows it.
- Subagent output is grounded:
  - candidates without valid evidence IDs are dropped
  - claims without valid evidence IDs are dropped
  - evidence without IDs is assigned deterministic provider-scoped IDs before public output
- No-LLM fallback produces deterministic evidence summaries and grounded candidates.
- Optional LLM analysis uses typed structured output validation before grounding.

## TDD Notes

First focused test run failed as expected before implementation:

```text
ModuleNotFoundError: No module named 'app.agents.subagents.attractions'
```

After implementation, focused tests passed.

## Verification

Focused Task 4 tests:

```text
.venv\Scripts\python.exe -m pytest tests/test_domain_subagents.py -q
7 passed in 0.25s
```

Task 4 plus relevant contract/tool/deep-search compatibility tests:

```text
.venv\Scripts\python.exe -m pytest tests/test_domain_subagents.py tests/test_subagent_contracts.py tests/test_subagent_tool_policy.py tests/test_deep_search_subgraph.py -q
28 passed in 0.33s
```

Syntax/import check:

```text
.venv\Scripts\python.exe -m compileall -q app/agents/subagents
exit 0
```

## Concerns

- The worktree had pre-existing `.superpowers/sdd` deletions/modifications, including Task 7 files, before this task started. They were not staged for this Task 4 commit.
- The required report file is written under `.superpowers/sdd/.../task-4-report.md` but is intentionally not included in the production/test commit.

## Review fixes

- Grounding now requires claim text, candidate factual fields, and summaries
  to be present in the referenced evidence text, not only to carry valid IDs.
- Deep Search exceptions are converted into an unavailable structured response
  with sanitized warnings.
- Subagent package exports are lazy to prevent the Deep Search/tool-policy
  import cycle.
- Grounding now checks each claim and candidate against the exact evidence IDs
  it cites, preventing cross-source evidence misattribution.
