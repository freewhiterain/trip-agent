# Task 1 Review Package

## Status
Working tree diff; no commits by user instruction.

## Diff Stat
```
 app/schemas/events.py | 26 +++++++++++++++++++++++++-
 1 file changed, 25 insertions(+), 1 deletion(-)
```

## Full Diff
```diff
diff --git a/app/schemas/events.py b/app/schemas/events.py
index 5e8760a..7e53d57 100644
--- a/app/schemas/events.py
+++ b/app/schemas/events.py
@@ -1,28 +1,52 @@
 """统一 SSE 事件契约。"""
 
 from datetime import datetime, timezone
 from typing import Any, Literal
 from uuid import uuid4
 
 from pydantic import BaseModel, Field
 
 
-SSEType = Literal["task", "plan", "worker", "evidence", "approval", "token", "result", "error", "done"]
+SSEType = Literal[
+    "task",
+    "plan",
+    "worker",
+    "evidence",
+    "approval",
+    "token",
+    "ask",
+    "result",
+    "error",
+    "done",
+    "tool_call",
+    "tool_result",
+]
 
 
 class SSEEvent(BaseModel):
     id: str = Field(default_factory=lambda: uuid4().hex)
     type: SSEType
     task_id: str | None = None
     conversation_id: str | None = None
     timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
     sequence: int | None = None
     payload: dict[str, Any] = Field(default_factory=dict)
 
     def legacy_payload(self) -> dict[str, Any]:
         result = self.model_dump(mode="json", exclude_none=True)
         if self.type == "token":
             result["content"] = self.payload.get("content", "")
         if self.type == "error":
             result["message"] = self.payload.get("message", "未知错误")
+        if self.type == "ask":
+            result["question"] = self.payload.get("question", "")
+            result["options"] = self.payload.get("options", [])
+        if self.type == "tool_call":
+            for field in ("tool", "call_id", "arguments"):
+                if field in self.payload:
+                    result[field] = self.payload[field]
+        if self.type == "tool_result":
+            for field in ("tool", "status", "result", "partial_values"):
+                if field in self.payload:
+                    result[field] = self.payload[field]
         return result
```

## New File: app/schemas/tools.py
```python
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class TripFormArguments(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    destination: str = Field(min_length=1, max_length=80)
    departure_date: date
    days: int = Field(ge=1, le=30)


class TripFormResult(TripFormArguments):
    pass


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

## New File: tests/test_main_agent_contracts.py
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

# Re-review Addendum

## Updated contract tests
```python
from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.events import SSEEvent
from app.schemas.tools import MainAgentDecision, ToolCallPayload, ToolResultRequest, TripFormResult


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
    assert event["arguments"] == {"initial_values": {}}


def test_tool_result_event_exposes_payload():
    request = ToolResultRequest(
        tool="collect_trip_requirements",
        status="completed",
        result=TripFormResult(destination="成都", departure_date=date(2026, 8, 10), days=4),
        partial_values={"destination": "成都"},
    )
    event = SSEEvent(type="tool_result", payload=request.model_dump()).legacy_payload()

    assert event["tool"] == "collect_trip_requirements"
    assert event["status"] == "completed"
    assert event["result"]["destination"] == "成都"
    assert event["result"]["departure_date"] == date(2026, 8, 10)
    assert event["result"]["days"] == 4
    assert event["partial_values"] == {"destination": "成都"}
```

# Serialization Fix Addendum

## Updated app/schemas/events.py
```python
"""统一 SSE 事件契约。"""

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


SSEType = Literal[
    "task",
    "plan",
    "worker",
    "evidence",
    "approval",
    "token",
    "ask",
    "result",
    "error",
    "done",
    "tool_call",
    "tool_result",
]


class SSEEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    type: SSEType
    task_id: str | None = None
    conversation_id: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sequence: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    def legacy_payload(self) -> dict[str, Any]:
        result = self.model_dump(mode="json", exclude_none=True)
        if self.type == "token":
            result["content"] = self.payload.get("content", "")
        if self.type == "error":
            result["message"] = self.payload.get("message", "未知错误")
        if self.type == "ask":
            result["question"] = self.payload.get("question", "")
            result["options"] = self.payload.get("options", [])
        if self.type == "tool_call":
            payload = result.get("payload", {})
            for field in ("tool", "call_id", "arguments"):
                if field in payload:
                    result[field] = payload[field]
        if self.type == "tool_result":
            payload = result.get("payload", {})
            for field in ("tool", "status", "result", "partial_values"):
                if field in payload:
                    result[field] = payload[field]
        return result
```

## Updated tests
```python
from datetime import date
import json

import pytest
from pydantic import ValidationError

from app.schemas.events import SSEEvent
from app.schemas.tools import MainAgentDecision, ToolCallPayload, ToolResultRequest, TripFormResult


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
    assert event["arguments"] == {"initial_values": {}}


def test_tool_result_event_exposes_payload():
    request = ToolResultRequest(
        tool="collect_trip_requirements",
        status="completed",
        result=TripFormResult(destination="成都", departure_date=date(2026, 8, 10), days=4),
        partial_values={"destination": "成都"},
    )
    event = SSEEvent(type="tool_result", payload=request.model_dump()).legacy_payload()

    assert event["tool"] == "collect_trip_requirements"
    assert event["status"] == "completed"
    assert event["result"]["destination"] == "成都"
    assert event["result"]["departure_date"] == "2026-08-10"
    assert event["result"]["days"] == 4
    assert event["partial_values"] == {"destination": "成都"}


def test_tool_result_legacy_payload_is_json_serializable():
    request = ToolResultRequest(
        tool="collect_trip_requirements",
        status="completed",
        result=TripFormResult(destination="Kyoto", departure_date=date(2026, 8, 10), days=4),
    )
    payload = SSEEvent(type="tool_result", payload=request.model_dump()).legacy_payload()

    json.dumps(payload)
```
