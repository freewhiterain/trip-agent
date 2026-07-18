from app.agents.workers.base import TravelWorker
from app.agents.workers.local_knowledge import load_destination_evidence
from app.schemas.planning import CandidateOption, ResearchTask, TravelRequirement, WorkerResult


class DestinationWorker(TravelWorker):
    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        evidence = load_destination_evidence(requirement.destination, "destination")
        if not evidence:
            return WorkerResult(
                task_id=task.id,
                worker="destination",
                status="partial",
                summary=f"尚无{requirement.destination}的本地知识文档。",
                warnings=["需要在实时搜索或知识库补充后生成事实性景点推荐。"],
            )
        return WorkerResult(
            task_id=task.id,
            worker="destination",
            status="completed",
            summary=f"已找到{requirement.destination}的本地目的地资料。",
            options=[CandidateOption(name=requirement.destination, category="destination", description="本地知识库已覆盖")],
            evidence=evidence,
        )
