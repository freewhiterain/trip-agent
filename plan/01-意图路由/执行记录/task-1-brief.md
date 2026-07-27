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

