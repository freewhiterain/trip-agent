from datetime import date

import pytest
from pydantic import ValidationError

from app.agents.planner import create_research_plan, parallel_groups
from app.agents.worker_tools import create_worker_tools
from app.schemas.planning import ResearchTask, TravelRequirement


def make_requirement(**overrides):
    data = {
        "origin": "Shanghai",
        "destination": "Chengdu",
        "departure_date": date(2026, 8, 1),
        "days": 5,
        "adults": 2,
        "budget": 6000,
        "styles": ["culture", "food"],
    }
    data.update(overrides)
    return TravelRequirement(**data)


def test_requirement_rejects_invalid_trip():
    with pytest.raises(ValidationError):
        make_requirement(destination="Shanghai")
    with pytest.raises(ValidationError):
        make_requirement(days=0)


def test_planner_creates_single_parallel_group_for_confirmed_destination():
    tasks = create_research_plan(make_requirement())

    assert {task.task_type for task in tasks} == {
        "attractions",
        "transport",
        "hotel",
        "food",
        "weather",
    }
    assert len({task.id for task in tasks}) == 5
    assert all(task.dependencies == [] for task in tasks)

    groups = parallel_groups(tasks)
    assert [{task.task_type for task in group} for group in groups] == [
        {"attractions", "transport", "hotel", "food", "weather"},
    ]


def test_planner_docstring_describes_single_independent_task_group():
    assert "single parallel group" in create_research_plan.__doc__
    assert "five independent" in create_research_plan.__doc__


def test_parallel_groups_respects_dependencies():
    first = ResearchTask(task_type="attractions", query="attractions")
    second = ResearchTask(task_type="hotel", query="hotel", dependencies=[first.id])

    groups = parallel_groups([second, first])

    assert [[task.id for task in group] for group in groups] == [[first.id], [second.id]]


def test_parallel_groups_rejects_cycles():
    first = ResearchTask(id="a", task_type="attractions", query="a", dependencies=["b"])
    second = ResearchTask(id="b", task_type="hotel", query="b", dependencies=["a"])

    with pytest.raises(ValueError, match="循环"):
        parallel_groups([first, second])


def test_five_workers_are_exposed_as_read_only_agent_tools():
    tools = create_worker_tools()

    assert {tool.name for tool in tools} == {
        "attractions_research_agent",
        "transport_research_agent",
        "hotel_research_agent",
        "food_research_agent",
        "weather_research_agent",
    }
    assert all("只读" in tool.description for tool in tools)
