from app.agents.workers.base import TravelWorker
from app.schemas.planning import CandidateOption, ResearchTask, TravelRequirement, WorkerResult


class TransportWorker(TravelWorker):
    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        preferences = requirement.transport_preferences or ["train", "flight", "driving"]
        options = [
            CandidateOption(
                name=value,
                category="transport",
                description="候选交通方式；班次、票价和余量必须通过实时数据源确认。",
            )
            for value in preferences
        ]
        return WorkerResult(
            task_id=task.id,
            worker="transport",
            status="partial",
            summary=f"已建立{requirement.origin}到{requirement.destination}的交通比较框架。",
            options=options,
            warnings=["当前未查询实时班次、票价或余量，不应据此购票。"],
        )
