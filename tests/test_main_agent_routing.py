import pytest

from app.services.main_agent import MainAgentService


@pytest.mark.asyncio
async def test_affirmation_after_offer_opens_form():
    decision = await MainAgentService(use_llm=False).decide(
        "好的",
        [{"role": "assistant", "content": "需要我帮你规划一下旅行吗？"}],
    )

    assert decision.action == "collect_trip_requirements"


@pytest.mark.asyncio
async def test_direct_plan_request_opens_prefilled_form():
    decision = await MainAgentService(use_llm=False).decide("帮我规划一次成都旅行", [])

    assert decision.action == "collect_trip_requirements"
    assert decision.initial_values["destination"] == "成都"
    assert "departure_date" not in decision.initial_values
    assert "days" not in decision.initial_values


@pytest.mark.asyncio
async def test_explicit_planning_takes_precedence_over_open_question_markers():
    decision = await MainAgentService(use_llm=False).decide("我最近想规划一次成都旅行", [])

    assert decision.action == "collect_trip_requirements"


@pytest.mark.asyncio
async def test_open_question_stays_rag_even_with_old_destination():
    context = [{"role": "tool", "content": '{"destination":"成都"}'}]

    decision = await MainAgentService(use_llm=False).decide("最近成都有什么好玩的？", context)

    assert decision.action == "answer_open_question"


@pytest.mark.asyncio
async def test_destination_recommendation_is_separate_action():
    decision = await MainAgentService(use_llm=False).decide("还没想好去哪，帮我推荐", [])

    assert decision.action == "recommend_destination"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["帮我推荐适合亲子游的地方", "不知道去哪", "去哪玩比较好"],
)
async def test_destination_recommendation_covers_uncertain_destination_requests(message):
    decision = await MainAgentService(use_llm=False).decide(message, [])

    assert decision.action == "recommend_destination"


@pytest.mark.asyncio
async def test_known_city_attraction_question_is_not_destination_recommendation():
    decision = await MainAgentService(use_llm=False).decide("成都有哪些适合亲子游的地方？", [])

    assert decision.action == "answer_open_question"


@pytest.mark.asyncio
async def test_affirmation_without_offer_is_direct_response():
    decision = await MainAgentService(use_llm=False).decide("好的", [])

    assert decision.action == "direct_response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "context",
    [
        [
            {"role": "assistant", "content": "需要我帮你规划一下旅行吗？"},
            {"role": "assistant", "content": "也可以先问我一个具体问题。"},
        ],
        [{"role": "assistant", "content": "需要我帮你规划一下旅行吗？现在开始吧。"}],
    ],
)
async def test_affirmation_requires_latest_exact_proactive_offer(context):
    decision = await MainAgentService(use_llm=False).decide("好的", context)

    assert decision.action == "direct_response"


@pytest.mark.asyncio
async def test_ambiguous_turn_uses_structured_llm_when_enabled(monkeypatch):
    class StructuredOutput:
        async def ainvoke(self, _messages):
            return {"action": "direct_response", "reason": "无明确规划意图"}

    class Llm:
        def with_structured_output(self, schema):
            assert schema.__name__ == "MainAgentDecision"
            return StructuredOutput()

    monkeypatch.setattr("app.services.main_agent.settings.dashscope_api_key", "configured")
    monkeypatch.setattr("app.agents.llm.get_llm", lambda: Llm())

    decision = await MainAgentService(use_llm=True).decide("我想想看", [])

    assert decision.action == "direct_response"


@pytest.mark.asyncio
async def test_prefill_never_calls_llm_or_fills_missing_date_and_days(monkeypatch):
    calls = []

    def unexpected_llm():
        calls.append(True)
        raise AssertionError("prefill must not call get_llm")

    monkeypatch.setattr("app.services.main_agent.settings.dashscope_api_key", "configured")
    monkeypatch.setattr("app.agents.llm.get_llm", unexpected_llm)

    decision = await MainAgentService(use_llm=True).decide("帮我规划一次成都旅行", [])

    assert decision.action == "collect_trip_requirements"
    assert decision.initial_values == {"destination": "成都"}
    assert calls == []


@pytest.mark.asyncio
async def test_enabled_llm_without_key_does_not_attempt_routing_model(monkeypatch):
    calls = []

    def unexpected_llm():
        calls.append(True)
        raise AssertionError("routing must not call get_llm without a key")

    monkeypatch.setattr("app.services.main_agent.settings.dashscope_api_key", "")
    monkeypatch.setattr("app.agents.llm.get_llm", unexpected_llm)

    decision = await MainAgentService(use_llm=True).decide("我想想看", [])

    assert decision.action == "direct_response"
    assert calls == []
