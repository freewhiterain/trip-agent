# Task 2 Report: Domain Tool Policies and Read-Only Adapters

## Implementation

- Added explicit per-worker `ToolPolicy` allowlists for weather, transport,
  attractions, hotel, and food.
- Added MCP provider-to-tool-name filtering in `MCPClientManager`.
- Added `ReadOnlyTool` wrappers so MCP, fallback API, local RAG, and bounded
  Deep Research results all pass through one normalizer.
- Added typed `ProviderError` responses and removed internal/raw credential
  metadata from returned `Evidence` objects.

## Verification

- `.venv\\Scripts\\python.exe -m pytest tests\\test_subagent_tool_policy.py -q`
  - `9 passed`.
- `.venv\\Scripts\\python.exe -m pytest tests\\test_subagent_tool_policy.py tests\\test_subagent_contracts.py -q`
  - `11 passed`.
- `.venv\\Scripts\\python.exe -m pytest tests\\test_phase1_planning_contracts.py tests\\test_phase2_external_reliability.py tests\\test_phase2_rag_workers.py -q`
  - `26 passed`, with two existing third-party warnings.

## Self-review

- Unknown provider names and unknown MCP tool names are ignored.
- Write-capable tools are never returned by the MCP allowlist.
- Raw provider exceptions and internal payload fields are replaced with typed,
  sanitized results.
- Scope is limited to Task 2 production and test files; existing archive and
  user-owned Task 7 files were not changed.
