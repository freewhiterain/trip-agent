from app.agents.workers.base import TravelWorker
from app.mcp_core.adapters.weather import AmapWeatherAdapter
from app.mcp_core.reliability import ExternalServiceError
from app.schemas.planning import ResearchTask, TravelRequirement, WorkerResult


class WeatherWorker(TravelWorker):
    def __init__(self, adapter: AmapWeatherAdapter | None = None):
        self.adapter = adapter

    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        if self.adapter is None:
            return WorkerResult(
                task_id=task.id,
                worker="weather",
                status="partial",
                summary=f"需要查询{requirement.destination}在{requirement.departure_date}附近的实时天气。",
                warnings=["实时天气工具未启用，未生成天气结论。"],
            )
        try:
            evidence = await self.adapter.query(requirement.destination, forecast=True)
        except ExternalServiceError as exc:
            return WorkerResult(
                task_id=task.id,
                worker="weather",
                status="partial",
                summary="实时天气查询失败。",
                warnings=[str(exc)],
            )
        return WorkerResult(
            task_id=task.id,
            worker="weather",
            status="completed" if evidence else "partial",
            summary=f"已查询{requirement.destination}实时天气。" if evidence else "天气服务未返回结果。",
            evidence=evidence,
            warnings=[] if evidence else ["天气服务返回空结果。"],
        )
