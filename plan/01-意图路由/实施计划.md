# Trip Main Agent Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current slot-driven chat flow with a main Agent that routes each turn to direct conversation, RAG, a persisted three-step form tool, or the travel-planning Supervisor.

**Architecture:** Keep FastAPI, PostgreSQL, SSE, LangGraph Supervisor, and the standalone HTML client. Add a dedicated main-Agent decision service and persisted tool-invocation model; the chat endpoint emits tool calls, a separate tool-result endpoint validates form submissions and invokes Supervisor, and the frontend renders/restores tool state. Existing automatic defaults and coordinator keyword routing are removed only after the new path passes tests.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic 2, SQLAlchemy 2 async, PostgreSQL, LangGraph, pytest, vanilla HTML/CSS/JavaScript, SSE.

## Global Constraints

- The main Agent is the only conversational router and reevaluates intent on every user turn.
- A new conversation starts with `需要我帮你规划一下旅行吗？`.
- `destination`, `departure_date`, and `days` are required before Supervisor execution; `days` is 1 through 30.
- Open questions call RAG only and never invoke the form or Supervisor.
- A direct planning request opens the form and prefills values extracted from that request.
- Choosing destination recommendation pauses and preserves the form, calls recommendation RAG, then restores the same tool invocation.
- Supervisor owns planning only; it invokes attractions, weather, transport, hotel, and food workers.
- Worker data-source selection remains unchanged in this plan except for the destination-to-attractions responsibility rename.
- Do not commit during execution unless the user explicitly requests a commit.
- Preserve unrelated working-tree changes.

---

## File Structure

- Create `app/schemas/tools.py`: tool names, invocation states, form arguments/results, and main-Agent decisions.
- Create `app/models/tool_invocation.py`: persisted call/result state and idempotency boundary.
- Create `app/governance/tool_invocations.py`: user-scoped repository operations.
- Create `app/services/main_agent.py`: per-turn intent decision, deterministic fallback, and prefill extraction.
- Create `app/api/v1/tools.py`: tool-result SSE endpoint.
- Modify `app/api/v1/chat.py`: delegate every turn to main Agent and emit tool calls or RAG answers.
- Modify `app/api/v1/conversations.py`: persist the proactive first assistant message.
- Modify `app/schemas/events.py`: expose `tool_call` and `tool_result` SSE contracts.
- Modify `app/schemas/planning.py`: require confirmed date and days; rename task type `destination` to `attractions`.
- Modify `app/agents/planner.py`, `app/agents/supervisor.py`, and worker registry: use the attractions responsibility name.
- Modify `1_zhixing.html`: render, submit, pause, and restore the three-step form tool.
- Replace obsolete phase-5/6 tests with behavior tests matching the approved design.

### Task 1: Define Tool And Main-Agent Contracts

**Files:**
- Create: `app/schemas/tools.py`
- Modify: `app/schemas/events.py`
- Test: `tests/test_main_agent_contracts.py`

**Interfaces:**
- Produces: `MainAgentAction`, `MainAgentDecision`, `TripFormArguments`, `TripFormResult`, `ToolCallPayload`, `ToolResultRequest`.
- Produces: SSE types `tool_call` and `tool_result` with top-level compatibility fields.

- [ ] **Step 1: Write failing schema tests**

```python
from datetime import date
import pytest
from pydantic import ValidationError

from app.schemas.events import SSEEvent
from app.schemas.tools import MainAgentDecision, TripFormResult, ToolCallPayload


def test_trip_form_requires_all_confirmed_fields():
    with pytest.raises(ValidationError):
        TripFormResult(destination="成都", departure_date=date(2026, 8, 10))
    assert TripFormResult(destination="成都", departure_date=date(2026, 8, 10), days=4).days == 4


def test_main_agent_decision_has_explicit_action():
    decision = MainAgentDecision(action="collect_trip_requirements", reason="用户明确要求规划")
    assert decision.action == "collect_trip_requirements"


def test_tool_call_event_exposes_payload():
    call = ToolCallPayload(call_id="call-1", tool="collect_trip_requirements", arguments={"initial_values": {}})
    event = SSEEvent(type="tool_call", payload=call.model_dump()).legacy_payload()
    assert event["tool"] == "collect_trip_requirements"
    assert event["call_id"] == "call-1"
```

- [ ] **Step 2: Run contract tests and verify import failures**

Run: `.venv\Scripts\python.exe -m pytest tests/test_main_agent_contracts.py -q`

Expected: FAIL because `app.schemas.tools` and the new SSE types do not exist.

- [ ] **Step 3: Implement strict schemas**

```python
MainAgentAction = Literal[
    "collect_trip_requirements",
    "answer_open_question",
    "recommend_destination",
    "direct_response",
]

class MainAgentDecision(BaseModel):
    action: MainAgentAction
    reason: str
    response: str | None = None
    initial_values: dict[str, Any] = Field(default_factory=dict)

class TripFormResult(BaseModel):
    destination: str = Field(min_length=1, max_length=80)
    departure_date: date
    days: int = Field(ge=1, le=30)

class ToolCallPayload(BaseModel):
    call_id: str
    tool: Literal["collect_trip_requirements"]
    arguments: dict[str, Any]

class ToolResultRequest(BaseModel):
    tool: Literal["collect_trip_requirements"]
    status: Literal["completed", "recommend_destination", "cancelled"]
    result: TripFormResult | None = None
    partial_values: dict[str, Any] = Field(default_factory=dict)
```

Extend `SSEType` and `legacy_payload()` so tool identity, `call_id`, arguments, status, and result are available to the standalone client.

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_main_agent_contracts.py tests/test_phase4_api_and_sse.py -q`

Expected: PASS.

- [ ] **Step 5: Review diff without committing**

Run: `git diff --check -- app/schemas/tools.py app/schemas/events.py tests/test_main_agent_contracts.py`

Expected: no output.

### Task 2: Persist Tool Invocations

**Files:**
- Create: `app/models/tool_invocation.py`
- Create: `app/governance/tool_invocations.py`
- Modify: `app/models/__init__.py`
- Test: `tests/test_tool_invocations.py`

**Interfaces:**
- Consumes: `ToolCallPayload`, `ToolResultRequest`.
- Produces: `ToolInvocationRecord`, `InMemoryToolInvocationRepository`, `PostgresToolInvocationRepository` with `create`, `get_for_user`, `update_partial`, and `complete_once`.

- [ ] **Step 1: Write repository behavior tests**

```python
@pytest.mark.asyncio
async def test_tool_result_is_idempotent():
    repository = InMemoryToolInvocationRepository()
    await repository.create(ToolInvocationRecord(call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements"))
    first = await repository.complete_once("c1", "u1", {"destination": "成都", "departure_date": "2026-08-10", "days": 4})
    second = await repository.complete_once("c1", "u1", first.result)
    assert first.status == "completed"
    assert second.version == first.version


@pytest.mark.asyncio
async def test_tool_call_is_user_scoped():
    repository = InMemoryToolInvocationRepository()
    await repository.create(ToolInvocationRecord(call_id="c1", user_id="u1", conversation_id="v1", tool="collect_trip_requirements"))
    assert await repository.get_for_user("c1", "u2") is None
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_tool_invocations.py -q`

Expected: FAIL because repository classes do not exist.

- [ ] **Step 3: Implement model and repositories**

Use a `tool_invocation` table with unique `call_id`, UUID user/conversation foreign keys, `tool`, `status`, JSON `arguments`, JSON `partial_values`, JSON nullable `result`, integer `version`, and timestamps. `complete_once` must lock or conditionally update only rows whose status is not `completed`; a duplicate completion returns the stored result without invoking downstream work again.

- [ ] **Step 4: Register the model and run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_tool_invocations.py tests/test_phase3_governance.py::test_governance_tables_are_registered_for_database_initialization -q`

Expected: PASS.

- [ ] **Step 5: Review diff without committing**

Run: `git diff --check -- app/models app/governance/tool_invocations.py tests/test_tool_invocations.py`

Expected: no output.

### Task 3: Implement Per-Turn Main-Agent Routing

**Files:**
- Create: `app/services/main_agent.py`
- Modify: `app/services/planning.py`
- Test: `tests/test_main_agent_routing.py`

**Interfaces:**
- Consumes: current user message and recent message context.
- Produces: `MainAgentService.decide(message: str, context: list[dict]) -> MainAgentDecision`.
- Reuses: `RequirementExtractor.extract()` only for safe form prefill, never for automatic Supervisor execution.

- [ ] **Step 1: Write routing tests**

```python
@pytest.mark.asyncio
async def test_affirmation_after_offer_opens_form():
    decision = await MainAgentService(use_llm=False).decide("好的", [{"role": "assistant", "content": "需要我帮你规划一下旅行吗？"}])
    assert decision.action == "collect_trip_requirements"


@pytest.mark.asyncio
async def test_direct_plan_request_opens_prefilled_form():
    decision = await MainAgentService(use_llm=False).decide("帮我规划一次成都旅行", [])
    assert decision.action == "collect_trip_requirements"
    assert decision.initial_values["destination"] == "成都"


@pytest.mark.asyncio
async def test_open_question_stays_rag_even_with_old_destination():
    context = [{"role": "tool", "content": '{"destination":"成都"}'}]
    decision = await MainAgentService(use_llm=False).decide("最近成都有什么好玩的？", context)
    assert decision.action == "answer_open_question"


@pytest.mark.asyncio
async def test_destination_recommendation_is_separate_action():
    decision = await MainAgentService(use_llm=False).decide("还没想好去哪，帮我推荐", [])
    assert decision.action == "recommend_destination"
```

- [ ] **Step 2: Verify tests fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_main_agent_routing.py -q`

Expected: FAIL because `MainAgentService` does not exist.

- [ ] **Step 3: Implement deterministic routing plus structured LLM fallback**

Apply high-confidence rules first: explicit planning verbs, affirmation only after the proactive offer, recommendation requests, and open-question markers. For ambiguous turns with a configured key, call `get_llm().with_structured_output(MainAgentDecision)` using an instruction that forbids using historical slots as intent. Without a key, return `direct_response` rather than guessing planning intent.

- [ ] **Step 4: Remove automatic defaults from requirement conversion**

Make `TravelRequirementDraft.to_requirement()` the only conversion used by chat planning. Delete `to_requirement_with_defaults`, `DEFAULT_DAYS`, and `DEFAULT_DEPARTURE_OFFSET_DAYS`; preserve extraction solely for form prefill.

- [ ] **Step 5: Run routing and requirement tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_main_agent_routing.py tests/test_phase1_planning_contracts.py -q`

Expected: PASS after replacing tests that asserted automatic defaults.

### Task 4: Make New Conversations Proactive

**Files:**
- Modify: `app/api/v1/conversations.py`
- Test: `tests/test_conversation_greeting.py`

**Interfaces:**
- Produces: every newly created conversation has exactly one persisted assistant greeting.

- [ ] **Step 1: Write failing API/service test**

Assert that conversation creation persists `需要我帮你规划一下旅行吗？` as an assistant `Message` and returns it as `initial_message`; assert retrying a read does not create another greeting.

- [ ] **Step 2: Verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_conversation_greeting.py -q`

Expected: FAIL because no greeting is created.

- [ ] **Step 3: Persist the greeting in the same transaction**

After creating and flushing the conversation, create one assistant message with `extra_info={"kind": "conversation_offer"}`. Include the serialized message in the create response so the frontend can render it immediately.

- [ ] **Step 4: Run test**

Run: `.venv\Scripts\python.exe -m pytest tests/test_conversation_greeting.py tests/test_phase0_api_compatibility.py -q`

Expected: PASS.

### Task 5: Route Chat Through Main Agent And Emit Tool Calls

**Files:**
- Modify: `app/api/v1/chat.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/test_chat_main_agent_flow.py`

**Interfaces:**
- Consumes: `MainAgentService`, `PostgresToolInvocationRepository`, `answer_open_question`.
- Produces: chat SSE branches `tool_call`, RAG `token`/`done`, and direct `token`/`done`.

- [ ] **Step 1: Write endpoint-stream tests with fakes**

Cover four paths: affirmation creates one persisted form call; direct planning prepopulates destination; open question calls only RAG; direct response calls neither RAG nor Supervisor. Assert no branch reads `TripDraft` to decide intent.

- [ ] **Step 2: Verify old behavior fails tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_chat_main_agent_flow.py -q`

Expected: FAIL because chat still uses `hard_missing`, `classify_intent`, and `TripCoordinator.route`.

- [ ] **Step 3: Replace chat orchestration**

Save the user message, load bounded recent conversation messages, call `MainAgentService.decide`, and switch only on its explicit action. For `collect_trip_requirements`, persist a tool invocation and emit:

```python
yield sse(event("tool_call", ToolCallPayload(
    call_id=call_id,
    tool="collect_trip_requirements",
    arguments={"initial_values": decision.initial_values},
).model_dump()))
yield sse(event("done"))
```

Do not invoke Supervisor from the normal chat-message endpoint.

- [ ] **Step 4: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_chat_main_agent_flow.py tests/test_main_agent_routing.py -q`

Expected: PASS.

### Task 6: Accept Tool Results And Invoke Supervisor

**Files:**
- Create: `app/api/v1/tools.py`
- Modify: `app/main.py`
- Modify: `app/api/v1/__init__.py`
- Test: `tests/test_trip_form_tool_flow.py`

**Interfaces:**
- Adds: `POST /api/v1/chat/tools/{call_id}/result` returning SSE.
- Consumes: `ToolResultRequest`, tool repository, `TripFormResult`, `run_travel_planning`.

- [ ] **Step 1: Write tool endpoint tests**

Test ownership, missing fields, invalid dates/days, duplicate completion, recommendation pause, and successful Supervisor invocation. A successful call must pass exactly the confirmed `destination`, `departure_date`, and `days`; no defaults are permitted.

- [ ] **Step 2: Verify tests fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_trip_form_tool_flow.py -q`

Expected: FAIL with route not found.

- [ ] **Step 3: Implement completion and SSE planning stream**

Validate ownership and tool name. For `recommend_destination`, save `partial_values`, call the recommendation RAG service, emit recommendation content plus a `tool_result` status of `awaiting_destination`, and leave invocation incomplete. For `completed`, atomically complete the call, create `TravelRequirement` from the validated result, invoke Supervisor once, persist task events and assistant output, then emit `result`, `token`, and `done`.

- [ ] **Step 4: Verify idempotency**

The second identical submission returns the stored result or task reference and must not call the fake Supervisor a second time.

- [ ] **Step 5: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_trip_form_tool_flow.py tests/test_tool_invocations.py -q`

Expected: PASS.

### Task 7: Rename Destination Research To Attractions

**Files:**
- Modify: `app/schemas/planning.py`
- Modify: `app/agents/planner.py`
- Modify: `app/agents/supervisor.py`
- Rename: `app/agents/workers/destination.py` to `app/agents/workers/attractions.py`
- Modify: `app/agents/workers/registry.py`
- Modify: `app/agents/workers/__init__.py`
- Test: `tests/test_phase1_planning_contracts.py`
- Test: `tests/test_phase1_supervisor.py`

**Interfaces:**
- Produces: `TaskType` includes `attractions` and excludes `destination`; `AttractionsWorker` returns `worker="attractions"`.

- [ ] **Step 1: Update tests to require five responsibilities**

Expected task order/grouping remains two groups, but the first task type is `attractions`. Assert the itinerary builder reads attraction candidates from the attractions result.

- [ ] **Step 2: Run tests and verify failures**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py -q`

Expected: FAIL on the old `destination` task type.

- [ ] **Step 3: Apply the responsibility rename**

Rename classes, imports, registry keys, planner task type, event payloads, and `_result_by_worker` lookup. Keep the current evidence source unchanged because Worker data-source design is deferred.

- [ ] **Step 4: Run Supervisor tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_phase1_planning_contracts.py tests/test_phase1_supervisor.py tests/test_phase6_coordinator.py -q`

Expected: Supervisor tests PASS; coordinator tests are removed or updated in Task 9 because coordinator is no longer part of chat routing.

### Task 8: Build And Restore The Three-Step Frontend Tool

**Files:**
- Modify: `1_zhixing.html`
- Test: `tests/test_frontend_trip_form.py`

**Interfaces:**
- Consumes: SSE `tool_call`; submits `ToolResultRequest` to `/api/v1/chat/tools/{call_id}/result`.
- Produces: persistent UI state keyed by `call_id` with `currentStep`, `values`, `errors`, `collapsed`, and `submitting`.

- [ ] **Step 1: Write static behavior tests**

Assert the HTML contains handlers for `tool_call`, history `extra_info`, `submitTripToolResult`, `restorePendingTool`, all three field names, date minimum, days range, and recommendation status. Assert the old `renderAskCard` path is absent.

- [ ] **Step 2: Verify tests fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_frontend_trip_form.py -q`

Expected: FAIL because only shortcut chips exist.

- [ ] **Step 3: Add stable form layout and controls**

Create one un-nested tool panel below the relevant assistant/tool message. Use a `1/3` progress label, full-row destination options plus custom text, native date input, 2/3/5/7 day options plus numeric input, back/next controls, collapse and close icon buttons, validation messages, and a submit lock. Keep the normal chat input usable.

- [ ] **Step 4: Implement SSE and submit handling**

On `tool_call`, render or update by `call_id`. Submit recommendation as `status="recommend_destination"` with partial values. Submit the final result as `status="completed"`; parse returned SSE with the existing buffered frame parser and render final tokens. Never call `sendMessage()` to submit form values.

- [ ] **Step 5: Restore from history**

Change history rendering to use `renderContent` for assistant messages and inspect `extra_info.tool_call` / `extra_info.tool_result`. Restore the latest non-completed invocation and its saved partial values after refresh or conversation switch.

- [ ] **Step 6: Run frontend tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_frontend_trip_form.py tests/test_phase4_api_and_sse.py::test_frontend_buffers_partial_sse_frames -q`

Expected: PASS.

### Task 9: Remove Obsolete Slot And Coordinator Chat Logic

**Files:**
- Delete: `app/services/intent.py`
- Remove from chat path: `app/agents/coordinator.py`
- Modify or delete: `tests/test_phase5_intent_and_ask.py`
- Modify or delete: `tests/test_phase5_generate_first.py`
- Modify or delete: `tests/test_phase6_coordinator.py`
- Modify: `README.md`

**Interfaces:**
- Removes: `classify_intent`, `hard_missing`, `to_requirement_with_defaults`, `DESTINATION_QUICK_OPTIONS`, chat `TripCoordinator.route`, and `ask` shortcut-card behavior.

- [ ] **Step 1: Search for obsolete production references**

Run: `rg -n "classify_intent|hard_missing|to_requirement_with_defaults|DESTINATION_QUICK_OPTIONS|TripCoordinator|renderAskCard|type.*ask" app 1_zhixing.html`

Expected: only code scheduled for removal or non-chat compatibility references.

- [ ] **Step 2: Remove old production paths**

Delete the old intent service and automatic default conversion. Remove coordinator use from chat; if no non-chat caller remains, delete coordinator and its tests. Remove the `ask` SSE type once no compatibility consumer remains.

- [ ] **Step 3: Replace obsolete tests**

Delete assertions that encode “destination alone is enough”, default date, default days, and keyword-based slice routing. Keep extractor tests only where extraction supports form prefill.

- [ ] **Step 4: Update README**

Document the main Agent routes, proactive greeting, Tool Call/Tool Result lifecycle, mandatory fields, Supervisor boundary, and deferred Worker data-source design. Remove claims that chat uses generate-first defaults.

- [ ] **Step 5: Verify obsolete symbols are gone**

Run: `rg -n "classify_intent|to_requirement_with_defaults|DEFAULT_DEPARTURE_OFFSET_DAYS|renderAskCard" app tests 1_zhixing.html`

Expected: no matches.

### Task 10: End-To-End Verification

**Files:**
- Test: `tests/test_main_agent_end_to_end.py`
- Modify only if a verified defect is found: files owned by Tasks 1 through 9.

**Interfaces:**
- Verifies the complete user-visible workflow without external network calls.

- [ ] **Step 1: Add end-to-end service tests**

Use in-memory/fake repositories and workers to verify these scenarios:

```text
new conversation -> proactive offer
"好的" -> form tool call
valid tool result -> Supervisor exactly once -> final response
"帮我规划成都旅行" -> prefilled form
"最近成都有什么好玩的" -> RAG only
recommend destination -> saved partial form -> city selected -> resumed form
refresh/history -> pending call restored
```

- [ ] **Step 2: Run the focused new suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_main_agent_contracts.py tests/test_tool_invocations.py tests/test_main_agent_routing.py tests/test_conversation_greeting.py tests/test_chat_main_agent_flow.py tests/test_trip_form_tool_flow.py tests/test_frontend_trip_form.py tests/test_main_agent_end_to_end.py -q`

Expected: PASS.

- [ ] **Step 3: Run the complete suite**

Run: `.venv\Scripts\python.exe -m pytest -q`

Expected: all tests PASS; only known dependency/cache warnings may remain.

- [ ] **Step 4: Run syntax and diff checks**

Run: `.venv\Scripts\python.exe -m compileall -q app`

Expected: exit code 0.

Run: `git diff --check`

Expected: no whitespace errors in changed files.

- [ ] **Step 5: Start and visually verify the application**

Run the existing startup path, open the standalone HTML client, and verify desktop and mobile widths. Confirm no overlapping controls, all text fits, the form can be collapsed/restored, and an open question never flashes the planning form. Use browser screenshots for the proactive greeting, each form step, recommendation pause, and final itinerary.

- [ ] **Step 6: Review changes without committing**

Run: `git status --short` and `git diff --stat`.

Expected: only approved implementation, test, and documentation files plus the user's pre-existing changes.
