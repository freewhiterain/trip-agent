"""Worker 注册表与最小权限调度。"""

from app.agents.workers.base import TravelWorker
from app.agents.workers.attractions import AttractionsWorker
from app.agents.workers.food import FoodWorker
from app.agents.workers.hotel import HotelWorker
from app.agents.workers.transport import TransportWorker
from app.agents.workers.weather import WeatherWorker
from app.config import settings
from app.mcp_core.adapters.weather import AmapWeatherAdapter
from app.mcp_core.adapters.search import TavilySearchAdapter
from app.research.deep_research import DeepResearchService
from app.schemas.planning import ResearchTask, TaskType, TravelRequirement, WorkerResult


class WorkerRegistry:
    def __init__(self, workers: dict[TaskType, TravelWorker]):
        self._workers = workers

    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        worker = self._workers.get(task.task_type)
        if worker is None:
            return WorkerResult(
                task_id=task.id,
                worker=task.task_type,
                status="failed",
                summary="没有可用的专业 Worker。",
                warnings=[f"未注册 Worker: {task.task_type}"],
            )
        try:
            return await worker.run(task, requirement)
        except Exception as exc:
            return WorkerResult(
                task_id=task.id,
                worker=task.task_type,
                status="failed",
                summary="Worker 执行失败。",
                warnings=[f"{type(exc).__name__}: {exc}"],
            )


def create_default_registry(enable_external: bool | None = None) -> WorkerRegistry:
    if enable_external is None:
        enable_external = settings.enable_external_tools
    weather_adapter = AmapWeatherAdapter() if enable_external else None
    research = (
        DeepResearchService(TavilySearchAdapter().search)
        if enable_external and settings.tavily_api_key
        else None
    )
    return WorkerRegistry(
        {
            "attractions": AttractionsWorker(research),
            "transport": TransportWorker(),
            "hotel": HotelWorker(),
            "food": FoodWorker(research),
            "weather": WeatherWorker(weather_adapter),
        }
    )
