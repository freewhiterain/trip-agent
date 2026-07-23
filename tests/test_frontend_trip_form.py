from pathlib import Path


HTML = Path("1_zhixing.html")


def read_page() -> str:
    return HTML.read_text(encoding="utf-8")


def test_trip_form_tool_call_and_submission_contract_is_present():
    html = read_page()

    assert 'parsed.type === "tool_call"' in html
    assert "renderTripTool" in html
    assert "submitTripToolResult" in html
    assert "/api/v1/chat/tools/${callId}/result" in html
    assert 'status: "recommend_destination"' in html
    assert 'status: "completed"' in html
    assert "parseSseStream" in html
    submit_start = html.index("function submitTripToolResult")
    submit_end = html.index("function restorePendingTool", submit_start)
    assert "sendMessage();" not in html[submit_start:submit_end]


def test_trip_form_has_three_steps_and_required_controls():
    html = read_page()

    assert "currentStep" in html
    assert "1/3" in html
    assert 'name="destination"' in html
    assert 'name="departure_date"' in html
    assert 'name="days"' in html
    assert 'type="date"' in html
    assert 'min="${today}"' in html
    assert "[2, 3, 5, 7]" in html
    assert "min=\"1\"" in html
    assert "max=\"30\"" in html
    assert "toggleTripTool" in html
    assert "closeTripTool" in html
    assert "submitting" in html


def test_history_renders_tool_state_and_old_ask_card_is_removed():
    html = read_page()

    assert "renderContent(msg.role, msg.content)" in html
    assert "extra_info.tool_call" in html
    assert "extra_info.tool_result" in html
    assert "restorePendingTool" in html
    assert "awaiting_destination" in html
    assert "renderAskCard" not in html
