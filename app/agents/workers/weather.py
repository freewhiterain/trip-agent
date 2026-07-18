from app.agents.workers.base import TravelWorker
from app.schemas.planning import ResearchTask, TravelRequirement, WorkerResult


class WeatherWorker(TravelWorker):
    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        return WorkerResult(
            task_id=task.id,
            worker="weather",
            status="partial",
            summary=f"需要查询{requirement.destination}在{requirement.departure_date}附近的实时天气。",
            warnings=["当前没有经过验证的实时天气结果，未生成天气结论。"],
        )
