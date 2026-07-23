"""阶段 0：规划边界和认证安全回归测试。"""

from datetime import date

import pytest

from app.agents.supervisor import run_travel_planning
from app.config import Settings, settings
from app.schemas.planning import TravelRequirement
from app.utils.security import create_access_token, decode_access_token


@pytest.mark.asyncio
async def test_planning_draft_exposes_no_transaction_data():
    draft = await run_travel_planning(
        TravelRequirement(origin="上海", destination="成都", departure_date=date(2026, 8, 1), days=2)
    )

    serialized = draft.model_dump_json()
    assert "订单" not in serialized
    assert "支付" not in serialized
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
