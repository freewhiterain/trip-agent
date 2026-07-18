"""确定性的旅行研究 Planner。"""

from app.schemas.planning import ResearchTask, TravelRequirement


def create_research_plan(requirement: TravelRequirement) -> list[ResearchTask]:
    """为已确定目的地的需求生成五个可并行研究任务。"""
    destination = requirement.destination
    common_criteria = ["返回结构化候选项", "事实结论附带 Evidence", "缺失实时数据时明确降级"]

    definitions = [
        (
            "destination",
            f"研究{destination}的景点、文化和适合的游览区域",
            ["hybrid_rag", "search"],
        ),
        (
            "transport",
            f"比较{requirement.origin}到{destination}在{requirement.departure_date}的交通方式",
            ["transport_api", "map"],
        ),
        (
            "hotel",
            f"研究{destination}适合本次行程的住宿区域与住宿类型",
            ["hotel_api", "map"],
        ),
        (
            "food",
            f"研究{destination}本地美食与用户饮食偏好匹配情况",
            ["hybrid_rag", "search"],
        ),
        (
            "weather",
            f"查询{destination}在{requirement.departure_date}前后的天气与出行条件",
            ["weather_api"],
        ),
    ]

    return [
        ResearchTask(
            task_type=task_type,
            query=query,
            required_tools=tools,
            completion_criteria=common_criteria,
        )
        for task_type, query, tools in definitions
    ]


def parallel_groups(tasks: list[ResearchTask]) -> list[list[ResearchTask]]:
    """按依赖关系生成可并行执行的任务组，并检测循环依赖。"""
    remaining = {task.id: task for task in tasks}
    completed: set[str] = set()
    groups: list[list[ResearchTask]] = []

    while remaining:
        ready = [
            task
            for task in remaining.values()
            if set(task.dependencies).issubset(completed)
        ]
        if not ready:
            raise ValueError("研究任务存在循环依赖或引用了不存在的依赖")
        groups.append(ready)
        for task in ready:
            completed.add(task.id)
            remaining.pop(task.id)

    return groups
