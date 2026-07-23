from app.agents.workers.base import TravelWorker
from app.agents.workers.local_knowledge import load_destination_evidence
from app.schemas.planning import CandidateOption, ResearchTask, TravelRequirement, WorkerResult
from app.research.deep_research import DeepResearchService


class AttractionsWorker(TravelWorker):
    def __init__(self, research: DeepResearchService | None = None):
        self.research = research

    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        evidence = load_destination_evidence(requirement.destination, "destination")
        warnings = []
        if not evidence and self.research is not None:
            report = await self.research.research(task.query)
            evidence = report.evidence
            warnings.extend(report.warnings)
            warnings.extend(report.conflicts)
        if not evidence:
            return WorkerResult(
                task_id=task.id,
                worker="attractions",
                status="partial",
                summary=f"尚无{requirement.destination}的本地知识文档。",
                warnings=["需要在实时搜索或知识库补充后生成事实性景点推荐。"],
            )
        return WorkerResult(
            task_id=task.id,
            worker="attractions",
            status="completed",
            summary=f"已找到{requirement.destination}的本地目的地资料。",
            options=[CandidateOption(name=requirement.destination, category="attractions", description="已获得可追溯研究资料")],
            evidence=evidence,
            warnings=warnings,
        )
