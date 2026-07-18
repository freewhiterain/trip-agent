"""Supervisor + Planner-Worker 的旅行规划主流程。"""

from __future__ import annotations

from datetime import timedelta
from operator import add
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.agents.planner import create_research_plan
from app.agents.workers import WorkerRegistry, create_default_registry
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


class SupervisorState(TypedDict, total=False):
    requirement: dict[str, Any]
    tasks: list[dict[str, Any]]
    task: dict[str, Any]
    worker_results: Annotated[list[dict[str, Any]], add]
    draft: dict[str, Any]
    status: str
    warnings: list[str]
    task_id: str
    user_id: str
    conversation_id: str | None


def _build_draft(
    requirement: TravelRequirement,
    results: list[WorkerResult],
) -> TravelPlanDraft:
    evidence = [item for result in results for item in result.evidence]
    warnings = [warning for result in results for warning in result.warnings]
    destination_result = next((r for r in results if r.worker == "destination"), None)
    food_result = next((r for r in results if r.worker == "food"), None)
    destination_title = (
        destination_result.options[0].name
        if destination_result and destination_result.options
        else requirement.destination
    )
    food_title = (
        food_result.options[0].name
        if food_result and food_result.options
        else "自由安排晚餐"
    )

    itinerary = []
    for offset in range(requirement.days):
        itinerary.append(
            ItineraryDay(
                day=offset + 1,
                date=requirement.departure_date + timedelta(days=offset),
                slots=[
                    TimeSlot(
                        period="morning",
                        title=f"{destination_title}分区游览",
                        description="根据已验证景点资料选择同一区域活动，减少折返。",
                    ),
                    TimeSlot(
                        period="afternoon",
                        title="文化或休闲活动",
                        description="具体场所需在实时开放信息接入后确认。",
                    ),
                    TimeSlot(
                        period="evening",
                        title=food_title,
                        description="结合饮食偏好选择，营业状态与价格需实时确认。",
                    ),
                ],
            )
        )

    budget = BudgetSummary(
        total_estimate=None,
        categories={
            "transport": None,
            "accommodation": None,
            "food": None,
            "attractions": None,
            "misc": None,
        },
        notes=[
            f"用户预算上限：{requirement.budget:.2f} 元" if requirement.budget else "用户未提供明确预算。",
            "实时价格数据接入前不生成虚假费用估算。",
        ],
    )
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
    """创建支持动态并行 Worker 的 LangGraph。"""
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
        await emit(state, "task_created", {"destination": requirement.destination})
        await emit(state, "plan_created", {"tasks": [task.model_dump(mode="json") for task in tasks]})
        return {
            "tasks": [task.model_dump(mode="json") for task in tasks],
            "status": "planned",
        }

    def route_to_workers(state: SupervisorState):
        tasks = state.get("tasks", [])
        if not tasks:
            return "synthesize"
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

    async def synthesizer_node(state: SupervisorState) -> dict[str, Any]:
        requirement = TravelRequirement.model_validate(state["requirement"])
        results = [WorkerResult.model_validate(value) for value in state.get("worker_results", [])]
        draft = _build_draft(requirement, results)
        await emit(state, "plan_generated", {"days": len(draft.itinerary), "warnings": len(draft.warnings)})
        await emit(state, "task_completed", {"status": "completed"})
        return {
            "draft": draft.model_dump(mode="json"),
            "status": "completed",
            "warnings": draft.warnings,
        }

    workflow = StateGraph(SupervisorState)
    workflow.add_node("planner", planner_node)
    workflow.add_node("run_worker", worker_node)
    workflow.add_node("synthesize", synthesizer_node)
    workflow.add_edge(START, "planner")
    workflow.add_conditional_edges(
        "planner",
        route_to_workers,
        ["run_worker", "synthesize"],
    )
    workflow.add_edge("run_worker", "synthesize")
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
    return TravelPlanDraft.model_validate(result["draft"])


async def create_supervisor_agent():
    """兼容 Agent 工厂的异步构造入口。"""
    return create_supervisor_graph()
