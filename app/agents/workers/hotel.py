from app.agents.workers.base import TravelWorker
from app.schemas.planning import CandidateOption, ResearchTask, TravelRequirement, WorkerResult


class HotelWorker(TravelWorker):
    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        preferences = requirement.accommodation_preferences or ["交通便利区域", "主要景点附近"]
        return WorkerResult(
            task_id=task.id,
            worker="hotel",
            status="partial",
            summary=f"已建立{requirement.destination}住宿筛选条件。",
            options=[CandidateOption(name=value, category="hotel_area", description="候选住宿方向，需实时核对价格与库存") for value in preferences],
            warnings=["当前未查询具体酒店价格、评分或库存。"],
        )
