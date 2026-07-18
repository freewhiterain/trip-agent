"""阶段 0：规划边界和认证安全回归测试。"""

from types import SimpleNamespace

import pytest

from app.agents.handoffs.step_config import get_step_config
from app.config import Settings, settings
from app.tools.state_transition import (
    confirm_plan_draft_tool,
    summarize_budget_tool,
)
from app.utils.security import create_access_token, decode_access_token


@pytest.mark.asyncio
async def test_legacy_agent_exposes_no_transaction_tools():
    config = await get_step_config()
    banned_fragments = {
        "order",
        "book",
        "payment",
        "refund",
        "订单",
        "支付",
        "预订",
        "退款",
    }

    assert "order_generation" not in config
    for step in config.values():
        tool_names = {tool.name.lower() for tool in step["tools"]}
        assert not any(
            fragment in tool_name
            for tool_name in tool_names
            for fragment in banned_fragments
        )


def test_budget_transitions_to_plan_review_without_order():
    runtime = SimpleNamespace(
        tool_call_id="budget-test",
        state={
            "user_requirement": {
                "adult_count": 2,
                "children_count": 0,
                "travel_days": 3,
            }
        },
    )

    command = summarize_budget_tool.func(runtime=runtime)

    assert command.update["current_step"] == "plan_review"
    assert command.update["plan_status"] == "draft"
    assert "order_id" not in command.update


def test_confirming_plan_has_no_external_side_effect_data():
    runtime = SimpleNamespace(tool_call_id="confirm-test", state={})

    command = confirm_plan_draft_tool.func(runtime=runtime)

    assert command.update["current_step"] == "planning_complete"
    assert command.update["plan_status"] == "confirmed"
    serialized = str(command.update)
    assert "ORDER-" not in serialized
    assert "pay.example.com" not in serialized


def test_jwt_uses_dedicated_secret(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret_key", "phase0-test-secret")
    monkeypatch.setattr(settings, "dashscope_api_key", "model-key-a")
    token = create_access_token({"sub": "user-1"})

    monkeypatch.setattr(settings, "dashscope_api_key", "model-key-b")
    assert decode_access_token(token)["sub"] == "user-1"


def test_production_rejects_default_jwt_secret():
    production = Settings(
        _env_file=None,
        APP_ENV="production",
        JWT_SECRET_KEY="development-only-change-me",
    )

    with pytest.raises(RuntimeError, match="JWT_SECRET_KEY"):
        production.validate_security()
