from app.agents.workers.base import TravelWorker
from app.agents.workers.local_knowledge import load_destination_evidence
from app.schemas.planning import CandidateOption, ResearchTask, TravelRequirement, WorkerResult


class FoodWorker(TravelWorker):
    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        evidence = load_destination_evidence(requirement.destination, "food")
        warnings = [] if evidence else ["缺少可验证的本地餐饮资料，未生成具体商家或价格。"]
        return WorkerResult(
            task_id=task.id,
            worker="food",
            status="completed" if evidence else "partial",
            summary=f"已整理{requirement.destination}餐饮研究资料。" if evidence else "餐饮研究需要补充数据源。",
            options=[CandidateOption(name="本地特色餐饮", category="food", description="具体餐厅需结合实时营业信息确认")] if evidence else [],
            evidence=evidence,
            warnings=warnings,
        )
