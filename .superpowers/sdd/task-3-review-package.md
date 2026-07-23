# Task 3 Review Package

No commits by user instruction. Full task-owned file contents follow.

## app/services/main_agent.py
```
"""Per-turn routing for the conversational travel entry point."""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.schemas.tools import MainAgentDecision
from app.services.planning import RequirementExtractor


_AFFIRMATIONS = {"好的", "好啊", "好呀", "可以", "开始吧", "行", "行啊", "好的开始吧"}
_PLANNING_MARKERS = ("规划", "安排行程", "制定行程", "做个行程", "做攻略", "旅行计划")
_OPEN_QUESTION_MARKERS = (
    "最近",
    "什么好玩",
    "好玩吗",
    "哪里好玩",
    "什么时候去",
    "天气",
    "气温",
    "热门景点",
    "介绍一下",
    "了解一下",
)
_PROACTIVE_OFFER = "需要我帮你规划一下旅行吗"


class MainAgentService:
    def __init__(self, use_llm: bool | None = None) -> None:
        self.use_llm = use_llm

    async def decide(self, message: str, context: list[dict[str, Any]]) -> MainAgentDecision:
        text = message.strip()
        normalized = self._normalize(text)

        if self._is_destination_recommendation(normalized):
            return MainAgentDecision(action="recommend_destination", reason="用户请求目的地推荐")
        if self._is_open_question(normalized):
            return MainAgentDecision(action="answer_open_question", reason="用户提出开放式旅行问题")
        if self._is_explicit_planning_request(normalized):
            return MainAgentDecision(
                action="collect_trip_requirements",
                reason="用户明确请求规划",
                initial_values=await self._prefill(text),
            )
        if normalized in _AFFIRMATIONS and self._has_recent_proactive_offer(context):
            return MainAgentDecision(action="collect_trip_requirements", reason="用户确认主动邀请")
        if self._llm_enabled():
            return await self._decide_with_llm(text, context)
        return MainAgentDecision(action="direct_response", reason="未识别到明确的规划意图")

    @staticmethod
    def _normalize(text: str) -> str:
        return "".join(text.split()).rstrip("。！？!?")

    @staticmethod
    def _is_destination_recommendation(text: str) -> bool:
        return "推荐" in text and any(
            marker in text
            for marker in ("没想好", "不知道去哪", "去哪", "去哪里", "目的地", "城市")
        )

    @staticmethod
    def _is_open_question(text: str) -> bool:
        return any(marker in text for marker in _OPEN_QUESTION_MARKERS)

    @staticmethod
    def _is_explicit_planning_request(text: str) -> bool:
        return any(marker in text for marker in _PLANNING_MARKERS)

    @staticmethod
    def _has_recent_proactive_offer(context: list[dict[str, Any]]) -> bool:
        for item in reversed(context):
            if item.get("role") != "assistant":
                continue
            if _PROACTIVE_OFFER in MainAgentService._normalize(str(item.get("content", ""))):
                return True
        return False

    def _llm_enabled(self) -> bool:
        return bool(settings.dashscope_api_key) if self.use_llm is None else self.use_llm

    async def _prefill(self, message: str) -> dict[str, Any]:
        draft = await RequirementExtractor().extract(message, use_llm=self._llm_enabled())
        return draft.model_dump(
            mode="json",
            include={"destination", "departure_date", "days"},
            exclude_none=True,
        )

    async def _decide_with_llm(
        self,
        message: str,
        context: list[dict[str, Any]],
    ) -> MainAgentDecision:
        try:
            from app.agents.llm import get_llm

            structured = get_llm().with_structured_output(MainAgentDecision)
            decision = MainAgentDecision.model_validate(
                await structured.ainvoke(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是旅行对话入口路由器。只根据本轮用户消息判断 action。"
                                "历史中的目的地、日期、天数和工具结果不得用来推断用户想规划。"
                                "只有用户当前明确要规划时才返回 collect_trip_requirements。"
                                "开放式提问返回 answer_open_question，目的地推荐返回 recommend_destination，"
                                "其他情况返回 direct_response。"
                            ),
                        },
                        {"role": "assistant", "content": f"最近上下文：{json.dumps(context, ensure_ascii=False)}"},
                        {"role": "user", "content": message},
                    ]
                )
            )
        except Exception:
            return MainAgentDecision(action="direct_response", reason="路由模型不可用，保守地直接回复")

        if decision.action != "collect_trip_requirements":
            return decision.model_copy(update={"initial_values": {}})
        return decision.model_copy(update={"initial_values": await self._prefill(message)})
```

## app/services/planning.py
```
"""自然语言需求提取与行程草稿展示。"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.config import settings
from app.schemas.planning import TravelPlanDraft, TravelRequirementDraft
from app.utils.logger import app_logger


CHINESE_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

RELATIVE_DATES = [("大后天", 3), ("后天", 2), ("明天", 1), ("今天", 0)]

GREETING_STOPWORDS = {"你好", "您好", "在吗", "谢谢", "好的", "可以", "没有", "不用", "嗯嗯", "哈喽", "再见"}

DESTINATION_QUICK_OPTIONS = ["哈尔滨", "成都", "大理", "厦门", "其他城市"]

KNOWN_DESTINATIONS = {
    "北京", "上海", "广州", "深圳", "杭州", "南京", "苏州", "成都", "重庆", "西安",
    "厦门", "青岛", "昆明", "丽江", "大理", "桂林", "阳朔", "三亚", "海口", "武汉",
    "长沙", "郑州", "天津", "沈阳", "长春", "哈尔滨", "乌鲁木齐", "拉萨", "西宁", "兰州",
    "银川", "呼和浩特", "贵阳", "南宁", "福州", "南昌", "合肥", "石家庄", "太原", "济南",
    "大连", "宁波", "无锡", "洛阳", "开封", "敦煌", "张家界", "黄山", "九寨沟", "威海",
    "烟台", "秦皇岛", "北戴河", "承德", "大同", "平遥", "凤凰", "香格里拉", "西双版纳",
    "稻城", "泸沽湖", "青海湖", "呼伦贝尔", "漠河", "延吉", "珠海", "汕头", "潮州", "顺德",
}


class RequirementExtractor:
    async def extract(
        self,
        text: str,
        today: date | None = None,
        use_llm: bool = True,
    ) -> TravelRequirementDraft:
        today = today or date.today()
        draft = self._extract_rules(text, today)
        if not draft.missing_fields() or not use_llm or not settings.dashscope_api_key:
            return draft
        try:
            from app.agents.llm import get_llm

            structured = get_llm().with_structured_output(TravelRequirementDraft)
            llm_draft = await structured.ainvoke(
                [
                    {
                        "role": "system",
                        "content": (
                            f"今天是 {today.isoformat()}。从用户文本提取国内旅行需求，以 JSON 格式输出。"
                            "相对日期（如明天、下周六）换算为具体日期。"
                            "未明确的信息必须返回 null，不得猜测日期、城市、预算或人数。"
                        ),
                    },
                    {"role": "user", "content": text},
                ]
            )
            return self._merge(draft, llm_draft)
        except Exception as exc:
            app_logger.warning(f"需求提取 LLM 兜底失败，退回规则结果: {type(exc).__name__}: {exc}")
            return draft

    @staticmethod
    def _merge(rules: TravelRequirementDraft, llm: TravelRequirementDraft) -> TravelRequirementDraft:
        """规则命中的字段优先（确定性高），LLM 只补规则漏掉的。"""
        values = rules.model_dump()
        llm_values = llm.model_dump()
        for field in ("origin", "destination", "departure_date", "days", "budget"):
            if values[field] is None:
                values[field] = llm_values[field]
        for field in ("styles", "special_needs"):
            merged = values[field] + [item for item in llm_values[field] if item not in values[field]]
            values[field] = merged
        return TravelRequirementDraft(**values)

    @staticmethod
    def _bare_destination(text: str) -> str | None:
        """用户单发一个地名（如“哈尔滨”）时直接识别为目的地。

        只认白名单或“××市/州/县”后缀，避免把“重新推荐”这类指令误判为地名；
        白名单外的城市由 LLM 兜底识别。
        """
        for line in reversed(text.splitlines()):
            candidate = line.strip().strip("，,。.!！?？~～ ")
            if not (2 <= len(candidate) <= 8 and re.fullmatch(r"[一-鿿]+", candidate)):
                continue
            if candidate in KNOWN_DESTINATIONS:
                return candidate
            if len(candidate) >= 3 and candidate[-1] in "市州县" and candidate not in GREETING_STOPWORDS:
                return candidate[:-1] if candidate[-1] == "市" else candidate
        return None

    @staticmethod
    def _extract_rules(text: str, today: date) -> TravelRequirementDraft:
        origin_match = re.search(r"(?:出发地|出发城市)[:：]?\s*([^，,。\s]{2,12})", text)
        if origin_match is None:
            origin_match = re.search(r"从([^，,。\s]{2,12}?)(?:出发|去)", text)
        destination_match = re.search(r"(?:目的地)[:：]?\s*([^，,。\s]{2,12})", text)
        if destination_match is None:
            destination_match = re.search(
                r"(?:规划|安排)(?:一次|一趟)?([^，,。\s\d一二三四五六七八九十]{2,12}?)(?:旅行|旅游|行程|之旅)",
                text,
            )
        if destination_match is None:
            destination_match = re.search(r"(?:去|规划|前往)([^，,。\s\d一二三四五六七八九十]{2,12}?)(?=\d|[一二三四五六七八九十]|旅|游|玩|，|,|。|$)", text)
        destination = destination_match.group(1) if destination_match else RequirementExtractor._bare_destination(text)
        days_match = re.search(r"(\d{1,2}|[一二三四五六七八九十])(?:天|日)(?:游|行程)", text)
        if days_match is None:
            days_match = re.search(r"(?:游玩|旅游|旅行|行程|玩)(\d{1,2}|[一二三四五六七八九十])天", text)
        budget_match = re.search(r"预算(?:约|大约|为)?\s*(\d+(?:\.\d+)?)", text)
        date_match = re.search(r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})日?", text)
        days = None
        if days_match:
            raw = days_match.group(1)
            days = int(raw) if raw.isdigit() else CHINESE_NUMBERS.get(raw)
        departure_date = None
        if date_match:
            departure_date = date(*(int(value) for value in date_match.groups()))
        else:
            for keyword, offset in RELATIVE_DATES:
                if keyword in text:
                    departure_date = today + timedelta(days=offset)
                    break
        styles = [keyword for keyword in ["文化", "美食", "亲子", "户外", "休闲", "自然"] if keyword in text]
        return TravelRequirementDraft(
            origin=origin_match.group(1) if origin_match else None,
            destination=destination,
            departure_date=departure_date,
            days=days,
            budget=float(budget_match.group(1)) if budget_match else None,
            styles=styles,
        )


def merge_drafts(new: TravelRequirementDraft, old: TravelRequirementDraft) -> TravelRequirementDraft:
    """槽位记忆合并：本轮新值覆盖旧值，未提及的字段沿用历史。"""
    return RequirementExtractor._merge(new, old)


def render_plan_markdown(draft: TravelPlanDraft) -> str:
    lines = [f"# {draft.requirement.destination}{draft.requirement.days}日行程草稿", ""]
    if draft.requirement.assumptions:
        lines.append("## 假设说明")
        lines.extend(f"- {assumption}" for assumption in draft.requirement.assumptions)
        lines.append("")
    for day in draft.itinerary:
        lines.append(f"## 第{day.day}天 · {day.date.isoformat()}")
        labels = {"morning": "上午", "afternoon": "下午", "evening": "晚上"}
        for slot in day.slots:
            lines.append(f"- **{labels[slot.period]}**：{slot.title}。{slot.description}")
        lines.append("")
    if draft.warnings:
        lines.append("## 数据说明")
        lines.extend(f"- {warning}" for warning in draft.warnings)
    lines.append("\n当前为可编辑规划草稿，不包含下单、预订或支付服务。")
    return "\n".join(lines)
```

## app/schemas/planning.py
```
"""多 Agent 旅行规划的公共数据契约。"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


TaskType = Literal["destination", "transport", "hotel", "food", "weather"]
WorkerStatus = Literal["completed", "partial", "failed"]


class TravelRequirement(BaseModel):
    """一次国内旅行规划所需的最小结构化需求。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    origin: str | None = Field(default=None, min_length=1, max_length=80)
    destination: str = Field(min_length=1, max_length=80)
    departure_date: date
    days: int = Field(ge=1, le=30)
    assumptions: list[str] = Field(default_factory=list)
    adults: int = Field(default=1, ge=1, le=30)
    children: int = Field(default=0, ge=0, le=20)
    budget: float | None = Field(default=None, gt=0)
    styles: list[str] = Field(default_factory=list)
    special_needs: list[str] = Field(default_factory=list)
    transport_preferences: list[str] = Field(default_factory=list)
    accommodation_preferences: list[str] = Field(default_factory=list)
    food_preferences: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def destination_must_differ_from_origin(self):
        if self.origin is not None and self.origin == self.destination:
            raise ValueError("出发地和目的地不能相同")
        return self


class TravelRequirementDraft(BaseModel):
    """自然语言需求提取的中间结果；缺失字段不会被模型臆造。"""

    origin: str | None = None
    destination: str | None = None
    departure_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=30)
    adults: int = Field(default=1, ge=1, le=30)
    children: int = Field(default=0, ge=0, le=20)
    budget: float | None = Field(default=None, gt=0)
    styles: list[str] = Field(default_factory=list)
    special_needs: list[str] = Field(default_factory=list)

    def missing_fields(self) -> list[str]:
        labels = {"origin": "出发地", "destination": "目的地", "departure_date": "出发日期", "days": "出行天数"}
        return [label for field, label in labels.items() if getattr(self, field) is None]

    def hard_missing(self) -> list[str]:
        """用于判断是否需要追问目的地。"""
        return ["目的地"] if self.destination is None else []

    def to_requirement(self) -> TravelRequirement:
        missing = self.missing_fields()
        if missing:
            raise ValueError(f"旅行需求缺少：{', '.join(missing)}")
        return TravelRequirement(**self.model_dump())

class TripDraftRecord(BaseModel):
    """会话级常驻行程草稿的持久化载体。"""

    user_id: str
    conversation_id: str
    version: int = 1
    requirement: dict[str, Any] = Field(default_factory=dict)
    content: dict[str, Any] = Field(default_factory=dict)


class ResearchTask(BaseModel):
    """Planner 生成的可调度研究任务。"""

    id: str = Field(default_factory=lambda: uuid4().hex)
    task_type: TaskType
    query: str
    dependencies: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    completion_criteria: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    """事实性结论的最小证据格式。"""

    content: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_url: str | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateOption(BaseModel):
    """Worker 返回的结构化候选项。"""

    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    category: str
    description: str = ""
    estimated_cost: float | None = Field(default=None, ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)


class WorkerResult(BaseModel):
    """所有专业 Worker 的统一输出。"""

    task_id: str
    worker: TaskType
    status: WorkerStatus
    summary: str
    options: list[CandidateOption] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TimeSlot(BaseModel):
    period: Literal["morning", "afternoon", "evening"]
    title: str
    description: str = ""
    estimated_cost: float | None = Field(default=None, ge=0)
    evidence_indexes: list[int] = Field(default_factory=list)


class ItineraryDay(BaseModel):
    day: int = Field(ge=1)
    date: date
    slots: list[TimeSlot]
    notes: list[str] = Field(default_factory=list)


class BudgetSummary(BaseModel):
    currency: str = "CNY"
    total_estimate: float | None = Field(default=None, ge=0)
    categories: dict[str, float | None] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class TravelPlanDraft(BaseModel):
    requirement: TravelRequirement
    itinerary: list[ItineraryDay]
    budget: BudgetSummary
    worker_results: list[WorkerResult]
    evidence: list[Evidence]
    warnings: list[str] = Field(default_factory=list)
    status: Literal["draft"] = "draft"
```

## tests/test_main_agent_routing.py
```
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
async def test_open_question_stays_rag_even_with_old_destination():
    context = [{"role": "tool", "content": '{"destination":"成都"}'}]

    decision = await MainAgentService(use_llm=False).decide("最近成都有什么好玩的？", context)

    assert decision.action == "answer_open_question"


@pytest.mark.asyncio
async def test_destination_recommendation_is_separate_action():
    decision = await MainAgentService(use_llm=False).decide("还没想好去哪，帮我推荐", [])

    assert decision.action == "recommend_destination"


@pytest.mark.asyncio
async def test_affirmation_without_offer_is_direct_response():
    decision = await MainAgentService(use_llm=False).decide("好的", [])

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
```

## tests/test_phase1_planning_contracts.py
```
from datetime import date

import pytest
from pydantic import ValidationError

from app.agents.planner import create_research_plan, parallel_groups
from app.agents.worker_tools import create_worker_tools
from app.schemas.planning import ResearchTask, TravelRequirement


def make_requirement(**overrides):
    data = {
        "origin": "上海",
        "destination": "成都",
        "departure_date": date(2026, 8, 1),
        "days": 5,
        "adults": 2,
        "budget": 6000,
        "styles": ["文化", "美食"],
    }
    data.update(overrides)
    return TravelRequirement(**data)


def test_requirement_rejects_invalid_trip():
    with pytest.raises(ValidationError):
        make_requirement(destination="上海")
    with pytest.raises(ValidationError):
        make_requirement(days=0)


def test_planner_creates_dag_with_two_parallel_groups():
    tasks = create_research_plan(make_requirement())

    assert {task.task_type for task in tasks} == {
        "destination",
        "transport",
        "hotel",
        "food",
        "weather",
    }
    assert len({task.id for task in tasks}) == 5

    groups = parallel_groups(tasks)
    assert [{task.task_type for task in group} for group in groups] == [
        {"destination", "weather"},
        {"transport", "hotel", "food"},
    ]


def test_parallel_groups_respects_dependencies():
    first = ResearchTask(task_type="destination", query="目的地")
    second = ResearchTask(task_type="hotel", query="住宿", dependencies=[first.id])

    groups = parallel_groups([second, first])

    assert [[task.id for task in group] for group in groups] == [[first.id], [second.id]]


def test_parallel_groups_rejects_cycles():
    first = ResearchTask(id="a", task_type="destination", query="a", dependencies=["b"])
    second = ResearchTask(id="b", task_type="hotel", query="b", dependencies=["a"])

    with pytest.raises(ValueError, match="循环依赖"):
        parallel_groups([first, second])


def test_five_workers_are_exposed_as_read_only_agent_tools():
    tools = create_worker_tools()

    assert {tool.name for tool in tools} == {
        "destination_research_agent",
        "transport_research_agent",
        "hotel_research_agent",
        "food_research_agent",
        "weather_research_agent",
    }
    assert all("只读" in tool.description for tool in tools)
```

# Fix Addendum

## Updated app/services/main_agent.py
```
"""Per-turn routing for the conversational travel entry point."""

from __future__ import annotations

import json
from typing import Any

from app.config import settings
from app.schemas.tools import MainAgentDecision
from app.services.planning import KNOWN_DESTINATIONS, RequirementExtractor


_AFFIRMATIONS = {"好的", "好啊", "好呀", "可以", "开始吧", "行", "行啊", "好的开始吧"}
_PLANNING_MARKERS = ("规划", "安排行程", "制定行程", "做个行程", "做攻略", "旅行计划")
_OPEN_QUESTION_MARKERS = (
    "最近",
    "什么好玩",
    "好玩吗",
    "哪里好玩",
    "什么时候去",
    "天气",
    "气温",
    "热门景点",
    "介绍一下",
    "了解一下",
)
_PROACTIVE_OFFER = "需要我帮你规划一下旅行吗"


class MainAgentService:
    def __init__(self, use_llm: bool | None = None) -> None:
        self.use_llm = use_llm

    async def decide(self, message: str, context: list[dict[str, Any]]) -> MainAgentDecision:
        text = message.strip()
        normalized = self._normalize(text)

        if self._is_explicit_planning_request(normalized):
            return MainAgentDecision(
                action="collect_trip_requirements",
                reason="用户明确请求规划",
                initial_values=await self._prefill(text),
            )
        if self._is_destination_recommendation(normalized):
            return MainAgentDecision(action="recommend_destination", reason="用户请求目的地推荐")
        if self._is_open_question(normalized):
            return MainAgentDecision(action="answer_open_question", reason="用户提出开放式旅行问题")
        if normalized in _AFFIRMATIONS and self._has_recent_proactive_offer(context):
            return MainAgentDecision(action="collect_trip_requirements", reason="用户确认主动邀请")
        if self._llm_enabled():
            return await self._decide_with_llm(text, context)
        return MainAgentDecision(action="direct_response", reason="未识别到明确的规划意图")

    @staticmethod
    def _normalize(text: str) -> str:
        return "".join(text.split()).rstrip("。！？!?")

    @staticmethod
    def _is_destination_recommendation(text: str) -> bool:
        if MainAgentService._mentions_known_destination(text):
            return False
        if any(
            marker in text
            for marker in ("没想好", "不知道去哪", "去哪玩", "去哪", "去哪里", "目的地", "城市")
        ):
            return True
        return "推荐" in text and any(
            marker in text for marker in ("地方", "目的地", "城市")
        )

    @staticmethod
    def _is_open_question(text: str) -> bool:
        if any(marker in text for marker in _OPEN_QUESTION_MARKERS):
            return True
        return MainAgentService._mentions_known_destination(text) and any(
            marker in text for marker in ("推荐", "亲子", "景点", "地方", "好玩", "玩")
        )

    @staticmethod
    def _is_explicit_planning_request(text: str) -> bool:
        return any(marker in text for marker in _PLANNING_MARKERS)

    @staticmethod
    def _has_recent_proactive_offer(context: list[dict[str, Any]]) -> bool:
        for item in reversed(context):
            if item.get("role") == "system":
                continue
            return (
                item.get("role") == "assistant"
                and MainAgentService._normalize(str(item.get("content", ""))) == _PROACTIVE_OFFER
            )
        return False

    def _llm_enabled(self) -> bool:
        return self.use_llm is not False and bool(settings.dashscope_api_key)

    async def _prefill(self, message: str) -> dict[str, Any]:
        draft = await RequirementExtractor().extract(message, use_llm=False)
        return draft.model_dump(
            mode="json",
            include={"destination", "departure_date", "days"},
            exclude_none=True,
        )

    @staticmethod
    def _mentions_known_destination(text: str) -> bool:
        return any(destination in text for destination in KNOWN_DESTINATIONS)

    async def _decide_with_llm(
        self,
        message: str,
        context: list[dict[str, Any]],
    ) -> MainAgentDecision:
        try:
            from app.agents.llm import get_llm

            structured = get_llm().with_structured_output(MainAgentDecision)
            decision = MainAgentDecision.model_validate(
                await structured.ainvoke(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是旅行对话入口路由器。只根据本轮用户消息判断 action。"
                                "历史中的目的地、日期、天数和工具结果不得用来推断用户想规划。"
                                "只有用户当前明确要规划时才返回 collect_trip_requirements。"
                                "开放式提问返回 answer_open_question，目的地推荐返回 recommend_destination，"
                                "其他情况返回 direct_response。"
                            ),
                        },
                        {"role": "assistant", "content": f"最近上下文：{json.dumps(context, ensure_ascii=False)}"},
                        {"role": "user", "content": message},
                    ]
                )
            )
        except Exception:
            return MainAgentDecision(action="direct_response", reason="路由模型不可用，保守地直接回复")

        if decision.action != "collect_trip_requirements":
            return decision.model_copy(update={"initial_values": {}})
        return decision.model_copy(update={"initial_values": await self._prefill(message)})
```

## Updated tests/test_main_agent_routing.py
```
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
```
