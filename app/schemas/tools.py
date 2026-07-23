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
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

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
