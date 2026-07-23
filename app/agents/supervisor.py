"""Supervisor + Planner-Worker 的旅行规划主流程。

图结构（确定性 DAG）：
    planner → dispatch ⇄ run_worker（按依赖分组扇出，组内并行）
            → route_planner → budget → synthesize → END

只有 synthesize 节点使用 LLM（可用时），其余全部是确定性函数。
"""

from __future__ import annotations

from datetime import timedelta
from operator import add
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field

from app.agents.planner import create_research_plan, parallel_groups
from app.agents.workers import WorkerRegistry, create_default_registry
from app.config import settings
from app.schemas.planning import (
    BudgetSummary,
    ItineraryDay,
    ResearchTask,
    TimeSlot,
    TravelPlanDraft,
    TravelRequirement,
    WorkerResult,
)
from app.governance.events import TaskEventService
from app.utils.logger import app_logger


class SupervisorState(TypedDict, total=False):
    requirement: dict[str, Any]
    tasks: list[dict[str, Any]]
    groups: list[list[str]]
    group_index: int
    task: dict[str, Any]
    worker_results: Annotated[list[dict[str, Any]], add]
    itinerary: list[dict[str, Any]]
    budget: dict[str, Any]
    draft: dict[str, Any]
    status: str
    warnings: list[str]
    task_id: str
    user_id: str
    conversation_id: str | None


def _result_by_worker(results: list[WorkerResult], worker: str) -> WorkerResult | None:
    return next((r for r in results if r.worker == worker), None)


def build_itinerary(
    requirement: TravelRequirement,
    results: list[WorkerResult],
) -> list[ItineraryDay]:
    """确定性路线编排：把候选项轮转分配到逐日时段。

    真实地理聚类与营业时间校验需要地图数据接入；当前按候选项轮转并明确标注。
    """
    attractions_result = _result_by_worker(results, "attractions")
    food_result = _result_by_worker(results, "food")
    attractions = attractions_result.options if attractions_result else []
    foods = food_result.options if food_result else []

    itinerary = []
    for offset in range(requirement.days):
        morning_title = (
            attractions[offset % len(attractions)].name
            if attractions
            else f"{requirement.destination}分区游览"
        )
        evening_title = foods[offset % len(foods)].name if foods else "自由安排晚餐"
        itinerary.append(
            ItineraryDay(
                day=offset + 1,
                date=requirement.departure_date + timedelta(days=offset),
                slots=[
                    TimeSlot(
                        period="morning",
                        title=morning_title,
                        description="根据已验证资料安排同一区域活动，减少折返；地理聚类待地图数据接入后精确化。",
                    ),
                    TimeSlot(
                        period="afternoon",
                        title="文化或休闲活动",
                        description="具体场所需在实时开放信息接入后确认。",
                    ),
                    TimeSlot(
                        period="evening",
                        title=evening_title,
                        description="结合饮食偏好选择，营业状态与价格需实时确认。",
                    ),
                ],
            )
        )
    return itinerary


def build_budget(requirement: TravelRequirement) -> BudgetSummary:
    """确定性预算汇总：只做用户输入的算术拆分，不虚构价格。"""
    notes = ["实时价格数据接入前不生成虚假费用估算。"]
    if requirement.budget:
        per_day = requirement.budget / requirement.days
        notes.insert(0, f"用户预算上限：{requirement.budget:.2f} 元，折合每天约 {per_day:.0f} 元。")
    else:
        notes.insert(0, "用户未提供明确预算。")
    return BudgetSummary(
        total_estimate=None,
        categories={
            "transport": None,
            "accommodation": None,
            "food": None,
            "attractions": None,
            "misc": None,
        },
        notes=notes,
    )


class _LLMItinerary(BaseModel):
    """LLM 综合输出的结构化行程。"""

    days: list[ItineraryDay] = Field(default_factory=list)


async def synthesize_itinerary_with_llm(
    requirement: TravelRequirement,
    results: list[WorkerResult],
    template: list[ItineraryDay],
) -> list[ItineraryDay]:
    """用 LLM 基于 Worker 证据润色行程；失败或无 Key 时退回模板。"""
    if not settings.dashscope_api_key:
        return template
    try:
        from app.agents.llm import get_llm

        evidence_digest = "\n".join(
            f"[{result.worker}] {result.summary} "
            + "；".join(option.name for option in result.options[:5])
            + ("；证据：" + "；".join(item.content[:120] for item in result.evidence[:3]) if result.evidence else "")
            for result in results
        )
        structured = get_llm(temperature=0.3).with_structured_output(_LLMItinerary)
        response = await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "你是行程编排专家，以 JSON 格式输出。只能使用给定研究结果中出现过的景点、餐厅和事实，"
                        "不得虚构任何班次、价格、营业时间或天气结论；"
                        "缺乏依据的时段保持通用描述并注明需实时确认。"
                        "保持天数、日期和 morning/afternoon/evening 三时段结构与模板一致。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"需求：{requirement.model_dump_json()}\n"
                        f"研究结果：\n{evidence_digest}\n"
                        f"模板行程：{[day.model_dump_json() for day in template]}\n"
                        "请在模板基础上，用研究结果改写每个时段的标题和描述。"
                    ),
                },
            ]
        )
        if len(response.days) == len(template):
            return response.days
        app_logger.warning("LLM 综合返回的天数与模板不一致，退回模板行程")
        return template
    except Exception as exc:
        app_logger.warning(f"LLM 行程综合失败，退回模板: {type(exc).__name__}: {exc}")
        return template


def assemble_draft(
    requirement: TravelRequirement,
    results: list[WorkerResult],
    itinerary: list[ItineraryDay],
    budget: BudgetSummary,
) -> TravelPlanDraft:
    evidence = [item for result in results for item in result.evidence]
    warnings = [warning for result in results for warning in result.warnings]
    return TravelPlanDraft(
        requirement=requirement,
        itinerary=itinerary,
        budget=budget,
        worker_results=results,
        evidence=evidence,
        warnings=list(dict.fromkeys(warnings)),
    )


def create_supervisor_graph(
    registry: WorkerRegistry | None = None,
    *,
    checkpointer=None,
    event_service: TaskEventService | None = None,
):
    """创建按依赖分组、组内并行的 LangGraph。"""
    registry = registry or create_default_registry()

    async def emit(state: SupervisorState, event_type: str, payload: dict | None = None):
        if event_service is not None:
            await event_service.emit(
                task_id=state["task_id"],
                user_id=state["user_id"],
                conversation_id=state.get("conversation_id"),
                event_type=event_type,
                payload=payload,
            )

    async def planner_node(state: SupervisorState) -> dict[str, Any]:
        requirement = TravelRequirement.model_validate(state["requirement"])
        tasks = create_research_plan(requirement)
        groups = [[task.id for task in group] for group in parallel_groups(tasks)]
        await emit(state, "task_created", {"destination": requirement.destination})
        await emit(state, "plan_created", {"tasks": [task.model_dump(mode="json") for task in tasks]})
        return {
            "tasks": [task.model_dump(mode="json") for task in tasks],
            "groups": groups,
            "group_index": 0,
            "status": "planned",
        }

    async def dispatch_node(state: SupervisorState) -> dict[str, Any]:
        return {}

    def route_group(state: SupervisorState):
        groups = state.get("groups", [])
        index = state.get("group_index", 0)
        if index >= len(groups):
            return "route_planner"
        task_ids = set(groups[index])
        tasks = [task for task in state.get("tasks", []) if task["id"] in task_ids]
        return [
            Send(
                "run_worker",
                {
                    "requirement": state["requirement"],
                    "task": task,
                    "worker_results": [],
                    "task_id": state["task_id"],
                    "user_id": state["user_id"],
                    "conversation_id": state.get("conversation_id"),
                },
            )
            for task in tasks
        ]

    async def worker_node(state: SupervisorState) -> dict[str, Any]:
        requirement = TravelRequirement.model_validate(state["requirement"])
        task = ResearchTask.model_validate(state["task"])
        await emit(state, "worker_started", {"task_id": task.id, "worker": task.task_type})
        result = await registry.run(task, requirement)
        await emit(state, "worker_completed", result.model_dump(mode="json"))
        if result.evidence:
            await emit(state, "evidence_collected", {"task_id": task.id, "count": len(result.evidence)})
        return {"worker_results": [result.model_dump(mode="json")]}

    async def advance_node(state: SupervisorState) -> dict[str, Any]:
        return {"group_index": state.get("group_index", 0) + 1}

    async def route_planner_node(state: SupervisorState) -> dict[str, Any]:
        requirement = TravelRequirement.model_validate(state["requirement"])
        results = [WorkerResult.model_validate(value) for value in state.get("worker_results", [])]
        itinerary = build_itinerary(requirement, results)
        await emit(state, "route_planned", {"days": len(itinerary)})
        return {"itinerary": [day.model_dump(mode="json") for day in itinerary]}

    async def budget_node(state: SupervisorState) -> dict[str, Any]:
        requirement = TravelRequirement.model_validate(state["requirement"])
        budget = build_budget(requirement)
        await emit(state, "budget_estimated", {"total_estimate": budget.total_estimate})
        return {"budget": budget.model_dump(mode="json")}

    async def synthesizer_node(state: SupervisorState) -> dict[str, Any]:
        requirement = TravelRequirement.model_validate(state["requirement"])
        results = [WorkerResult.model_validate(value) for value in state.get("worker_results", [])]
        template = [ItineraryDay.model_validate(value) for value in state.get("itinerary", [])]
        budget = BudgetSummary.model_validate(state["budget"])
        itinerary = await synthesize_itinerary_with_llm(requirement, results, template)
        draft = assemble_draft(requirement, results, itinerary, budget)
        await emit(state, "plan_generated", {"days": len(draft.itinerary), "warnings": len(draft.warnings)})
        await emit(state, "task_completed", {"status": "completed"})
        return {
            "draft": draft.model_dump(mode="json"),
            "status": "completed",
            "warnings": draft.warnings,
        }

    workflow = StateGraph(SupervisorState)
    workflow.add_node("planner", planner_node)
    workflow.add_node("dispatch", dispatch_node)
    workflow.add_node("run_worker", worker_node)
    workflow.add_node("advance", advance_node)
    workflow.add_node("route_planner", route_planner_node)
    workflow.add_node("budget", budget_node)
    workflow.add_node("synthesize", synthesizer_node)
    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "dispatch")
    workflow.add_conditional_edges("dispatch", route_group, ["run_worker", "route_planner"])
    workflow.add_edge("run_worker", "advance")
    workflow.add_edge("advance", "dispatch")
    workflow.add_edge("route_planner", "budget")
    workflow.add_edge("budget", "synthesize")
    workflow.add_edge("synthesize", END)
    return workflow.compile(checkpointer=checkpointer)


async def run_travel_planning(
    requirement: TravelRequirement,
    registry: WorkerRegistry | None = None,
    *,
    checkpointer=None,
    event_service: TaskEventService | None = None,
    task_id: str | None = None,
    user_id: str = "anonymous",
    conversation_id: str | None = None,
) -> TravelPlanDraft:
    """供 API、测试和后台任务调用的结构化规划入口。"""
    task_id = task_id or uuid4().hex
    graph = create_supervisor_graph(registry, checkpointer=checkpointer, event_service=event_service)
    try:
        result = await graph.ainvoke(
            {
                "requirement": requirement.model_dump(mode="json"),
                "worker_results": [],
                "warnings": [],
                "task_id": task_id,
                "user_id": user_id,
                "conversation_id": conversation_id,
            },
            config={"configurable": {"thread_id": task_id}},
        )
    except Exception as exc:
        if event_service is not None:
            await event_service.emit(
                task_id=task_id,
                user_id=user_id,
                conversation_id=conversation_id,
                event_type="task_failed",
                payload={"error": type(exc).__name__},
            )
        raise
    return TravelPlanDraft.model_validate(result["draft"])


async def create_supervisor_agent():
    """兼容 Agent 工厂的异步构造入口。"""
    return create_supervisor_graph()
