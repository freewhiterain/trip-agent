from app.agents.workers.base import TravelWorker
from app.agents.workers.local_knowledge import LocalKnowledgeService, get_local_knowledge_service
from app.agents.workers.rag_analysis import analyze_worker_evidence, worker_result_from_analysis
from app.schemas.planning import ResearchTask, TravelRequirement, WorkerResult


class TransportWorker(TravelWorker):
    def __init__(self, knowledge: LocalKnowledgeService | None = None, llm=None):
        self.knowledge = knowledge
        self.llm = llm

    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        if requirement.origin is None:
            return WorkerResult(
                task_id=task.id,
                worker="transport",
                status="partial",
                summary="出发地未提供，未生成跨城交通方案。",
                warnings=["补充出发地后才能检索跨城交通证据。"],
                is_mock=True,
            )
        query = f"{requirement.origin or 'origin pending'} {task.query} {' '.join(requirement.transport_preferences)}"
        evidence = (self.knowledge or get_local_knowledge_service()).search_destination(
            requirement.destination, "transport", query
        )
        analysis = await analyze_worker_evidence("transport", task, requirement, evidence, llm=self.llm)
        return worker_result_from_analysis(task, "transport", evidence, analysis)
