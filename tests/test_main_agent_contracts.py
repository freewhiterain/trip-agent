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
