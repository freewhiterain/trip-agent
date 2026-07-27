## Spec verdict

Approved.

The scoped fixes satisfy the Task 2 brief. The explicit worker policies are intact, MCP exposure remains allowlisted by provider/tool name, non-MCP providers are now surfaced as read-only wrappers, and all tool invocations pass through the shared normalizer before a subagent receives output.

Evidence checked:

- `app/agents/subagents/tool_policy.py:10` defines the explicit five-worker policy matrix.
- `app/mcp_core/client.py:68` filters MCP tools to known read-only tool names only.
- `app/agents/subagents/tools.py:25` defines `ReadOnlyTool`; `app/agents/subagents/tools.py:33` normalizes invocation results and exceptions.
- `app/agents/subagents/tools.py:143`, `app/agents/subagents/tools.py:157`, and `app/agents/subagents/tools.py:173` add wrapped `local_rag`, `weather_fallback_api`, and `deep_research` adapters.
- `app/agents/subagents/tools.py:47` and `app/agents/subagents/tools.py:53` strip hidden metadata keys before returning `Evidence`.

## Quality verdict

Approved.

The updated tests cover the prior gaps at an appropriate level for this task: full policy matrix coverage, MCP allowlist filtering, builder-level wrapper coverage for MCP plus non-MCP providers, metadata scrubbing, and sanitized provider errors. I did not find remaining blockers in the scoped re-review.

Verification run:

- `.venv\Scripts\python.exe -m pytest tests\test_subagent_tool_policy.py -q -p no:cacheprovider`
  - Result: `9 passed in 0.16s`
- `.venv\Scripts\python.exe -m pytest tests\test_subagent_tool_policy.py tests\test_subagent_contracts.py -q -p no:cacheprovider`
  - Result: `11 passed in 0.17s`

## Prior findings verification

1. Non-MCP providers are now wrapped: resolved by `local_rag`, `weather_fallback_api`, and `deep_research` `ReadOnlyTool` adapters in `app/agents/subagents/tools.py:143`, `app/agents/subagents/tools.py:157`, and `app/agents/subagents/tools.py:173`.
2. MCP invocations are normalized: resolved by wrapping allowlisted MCP tools in `ReadOnlyTool` and routing `ainvoke()` through `normalize_tool_result()` in `app/agents/subagents/tools.py:33` and `app/agents/subagents/tools.py:129`.
3. Hidden `Evidence.metadata` is stripped: resolved by `_HIDDEN_METADATA_KEYS` and `_sanitize_evidence()` in `app/agents/subagents/tools.py:47` and `app/agents/subagents/tools.py:53`.
4. Tests cover the adapter contract: resolved by builder and metadata tests in `tests/test_subagent_tool_policy.py:64` and `tests/test_subagent_tool_policy.py:96`, plus full matrix coverage in `tests/test_subagent_tool_policy.py:27`.
5. Implementer report is now present at the requested path and was reviewed.

## Remaining findings

None.

## Approval decision

Approved.
