# Trip Agent Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the travel agent's production research path, evidence governance, editable trip workspace, scheduling, frontend protocol, and deployment hardening.

**Architecture:** Use one mode-aware Subagent Registry for Supervisor and Agent-as-Tool. Keep Deep Search as a bounded, policy-controlled capability inside a domain subagent. Treat governed evidence and the durable Trip Draft as the only inputs to scheduling and user-facing output.

**Tech Stack:** Python 3, FastAPI, Pydantic, SQLAlchemy/PostgreSQL, LangGraph, SSE, pytest, existing RAG and MCP adapters.

## Global Constraints

- Preserve all existing user changes, especially `.superpowers/sdd` modifications and uncommitted Agent-as-Tool files.
- Do not add booking, payment, cancellation, or message-delivery behavior.
- Do not fabricate real-time prices, inventory, schedules, or weather when providers are unavailable.
- Keep Deep Search bounded by the existing request limits and worker policy.
- Every downstream factual claim must retain valid evidence IDs.
- Each phase must pass its focused tests before its commit.

---

### Task 1: Establish the canonical production registry

**Files:**
- Modify: `app/agents/factory.py`
- Modify: `app/agents/subagents/registry.py`
- Modify: `app/agents/supervisor.py`
- Modify: `app/agents/agent_tools.py`
- Test: `tests/test_agent_factory.py`
- Test: `tests/test_agent_as_tool.py`

**Interfaces:**
- `create_planning_registry()` returns the configured canonical registry and fallback metadata.
- `AgentToolRegistry` accepts that registry through dependency injection and does not create a second default runtime when one is available.

- [ ] Add failing tests proving default production planning selects the canonical Subagent Registry and Agent-as-Tool can use the same instance.
- [ ] Run `pytest tests/test_agent_factory.py tests/test_agent_as_tool.py -q` and verify the new assertions fail.
- [ ] Update the factory and Agent Tool construction path; keep legacy mode explicit and observable.
- [ ] Run the focused tests and verify they pass.
- [ ] Run the existing supervisor and subagent tests.
- [ ] Commit with `refactor: unify production worker registry`.

### Task 2: Make Deep Search explicit and bounded

**Files:**
- Modify: `app/schemas/planning.py`
- Modify: `app/research/deep_search.py`
- Modify: `app/agents/subagents/base.py`
- Modify: `app/agents/subagents/tools.py`
- Modify: `app/agents/agent_tools.py`
- Test: `tests/test_deep_search.py`
- Test: `tests/test_agent_as_tool.py`

**Interfaces:**
- `ResearchTask` exposes an explicit research mode with normal and deep values.
- `DomainSubagent.run()` honors explicit deep mode only when `ToolPolicy` allows it.
- The old fixed pipeline is either removed from the active path or becomes a clearly named compatibility adapter.

- [ ] Add tests for explicit deep mode, policy denial, provider failure, round limits, and normal-mode fallback.
- [ ] Run the focused tests and verify the new cases fail.
- [ ] Route explicit deep mode through `run_deep_search()` and remove duplicate nested execution.
- [ ] Return a policy warning for weather/transport deep requests instead of claiming Deep Search ran.
- [ ] Run focused and existing research tests.
- [ ] Commit with `feat: make deep search explicit and bounded`.

### Task 3: Complete evidence arbitration and scheduling safety

**Files:**
- Modify: `app/governance/evidence.py`
- Modify: `app/schemas/research.py`
- Modify: `app/agents/supervisor.py`
- Modify: `app/schemas/events.py`
- Test: `tests/test_evidence_governance.py`
- Test: `tests/test_supervisor.py`

**Interfaces:**
- `ReviewedResearch` exposes safe facts and blocked conflict keys.
- Scheduler input contains only governed facts; unresolved conflicts remain visible as warnings.

- [ ] Add tests proving conflicting facts are not scheduled and source metadata determines ranking.
- [ ] Run the focused tests and verify they fail.
- [ ] Implement provider/source normalization, deterministic ranking, conflict blocking, and warning propagation.
- [ ] Ensure final drafts include evidence IDs for every scheduled fact.
- [ ] Run governance and supervisor tests.
- [ ] Commit with `fix: block unresolved evidence conflicts`.

### Task 4: Connect task state and Trip Draft persistence

**Files:**
- Modify: `app/api/v1/planning.py`
- Modify: `app/api/v1/chat.py`
- Modify: `app/governance/drafts.py`
- Modify: `app/schemas/planning.py`
- Modify: `app/models/draft.py`
- Modify: `app/models/task.py`
- Test: `tests/test_planning_api.py`
- Test: `tests/test_drafts.py`
- Test: `tests/test_chat.py`

**Interfaces:**
- Task responses expose accurate lifecycle status and enforce user/conversation ownership.
- Draft repository reads and writes the current version for a conversation.
- Chat edits operate on the current draft through typed operations.

- [ ] Add failing tests for task ownership, failed status, draft version increments, and reconnect recovery.
- [ ] Run focused API and draft tests and verify failure.
- [ ] Wire the repository into planning and chat; validate task/conversation/user relationships.
- [ ] Persist SSE tool results before terminal events.
- [ ] Implement typed local edit handling for day/activity changes.
- [ ] Run focused and existing API tests.
- [ ] Commit with `feat: persist trip workspace and task state`.

### Task 5: Replace the fixed itinerary template

**Files:**
- Modify: `app/agents/supervisor.py`
- Create: `app/agents/scheduling.py`
- Create: `app/schemas/scheduling.py`
- Test: `tests/test_scheduling.py`
- Test: `tests/test_supervisor.py`

**Interfaces:**
- `schedule_itinerary(governed_research, requirement) -> ScheduledItinerary`.
- `calculate_budget(schedule, budget) -> BudgetSummary`.

- [ ] Add tests for time windows, travel buffers, duplicate locations, weather constraints, missing data, and budget totals.
- [ ] Run the focused scheduling tests and verify failure.
- [ ] Implement deterministic constraint-aware scheduling with explicit unscheduled reasons.
- [ ] Include transport, hotel location, weather, food, attraction, and evidence references when available.
- [ ] Replace template generation and calculate itemized budget totals.
- [ ] Run scheduling and supervisor tests.
- [ ] Commit with `feat: add constraint-aware itinerary scheduling`.

### Task 6: Complete frontend event and startup integration

**Files:**
- Modify: `1_zhixing.html`
- Modify: `app/main.py`
- Modify: `start.bat`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `tests/test_frontend_contract.py`

**Interfaces:**
- Frontend handles `agent_tool_call`, `tool_result`, `subagent_started`, `subagent_tool_call`, `follow_up_search`, `evidence_collected`, `research_conflict`, and `subagent_completed`.
- FastAPI serves the frontend from the configured application entrypoint.

- [ ] Add protocol tests for Agent Tool and research events.
- [ ] Run the frontend contract tests and verify failure.
- [ ] Implement event rendering, evidence/warning display, and reconnect state recovery.
- [ ] Mount the HTML entrypoint and unify port configuration.
- [ ] Remove unsupported booking claims from user-facing copy.
- [ ] Run API contract tests and a local startup smoke test.
- [ ] Commit with `feat: complete agent research frontend flow`.

### Task 7: Harden security and external integration coverage

**Files:**
- Modify: `app/config.py`
- Modify: `app/main.py`
- Modify: `app/core/checkpointer.py`
- Modify: `app/core/store.py`
- Modify: `app/api/v1/auth.py`
- Modify: `app/utils/logger.py`
- Modify: `README.md`
- Test: `tests/test_security_config.py`
- Test: `tests/test_external_integrations.py`

**Interfaces:**
- Production configuration rejects unsafe defaults and constructs escaped database/Redis URLs.
- Authentication and external calls expose bounded, auditable failure behavior.

- [ ] Add tests for production secret validation, CORS allowlist, URL escaping, singleton reset, and redacted errors.
- [ ] Run security tests and verify failure.
- [ ] Implement configuration validation, CORS allowlist, rate-limit seams, token claims, redacted logging, and close/reset behavior.
- [ ] Add opt-in integration tests for MCP, search, weather, Ollama, and vector-store compatibility.
- [ ] Update setup documentation to describe mock and real-provider modes accurately.
- [ ] Run the complete test suite and report skipped external tests explicitly.
- [ ] Commit with `chore: harden deployment and integration coverage`.

### Task 8: Final verification and push

**Files:**
- Modify only files required by verification failures.

- [ ] Inspect `git diff` and confirm unrelated user changes remain untouched.
- [ ] Run focused tests for every phase.
- [ ] Run the full test suite with the supported project command.
- [ ] Run a startup and chat-stream smoke test if local services are available.
- [ ] Commit any final test-only fixes to `main`.
- [ ] Push `main` to `origin` and report the exact remote result.

