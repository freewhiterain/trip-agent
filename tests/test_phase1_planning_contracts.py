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


def test_planner_creates_five_independent_worker_tasks():
    tasks = create_research_plan(make_requirement())

    assert {task.task_type for task in tasks} == {
        "destination",
        "transport",
        "hotel",
        "food",
        "weather",
    }
    assert len({task.id for task in tasks}) == 5
    assert len(parallel_groups(tasks)) == 1


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
