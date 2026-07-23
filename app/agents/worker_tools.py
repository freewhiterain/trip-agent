"""将专业 Worker 暴露为 Supervisor 可调用的结构化只读工具。"""

from collections.abc import Callable

from langchain_core.tools import StructuredTool

from app.agents.workers import WorkerRegistry, create_default_registry
from app.schemas.planning import ResearchTask, TaskType, TravelRequirement


WORKER_TOOL_NAMES: dict[TaskType, str] = {
    "attractions": "attractions_research_agent",
    "transport": "transport_research_agent",
    "hotel": "hotel_research_agent",
    "food": "food_research_agent",
    "weather": "weather_research_agent",
}


def _build_coroutine(task_type: TaskType, registry: WorkerRegistry) -> Callable:
    async def invoke_worker(
        task: ResearchTask,
        requirement: TravelRequirement,
    ) -> dict:
        if task.task_type != task_type:
            raise ValueError(f"工具 {WORKER_TOOL_NAMES[task_type]} 不能执行 {task.task_type} 任务")
        result = await registry.run(task, requirement)
        return result.model_dump(mode="json")

    return invoke_worker


def create_worker_tools(registry: WorkerRegistry | None = None) -> list[StructuredTool]:
    """创建五个只读 Agents-as-tools。"""
    registry = registry or create_default_registry()
    return [
        StructuredTool.from_function(
            coroutine=_build_coroutine(task_type, registry),
            name=tool_name,
            description=f"执行{task_type}领域的只读旅行研究，返回结构化 WorkerResult 和 Evidence。",
        )
        for task_type, tool_name in WORKER_TOOL_NAMES.items()
    ]
