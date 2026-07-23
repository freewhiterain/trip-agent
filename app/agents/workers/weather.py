from app.agents.workers.base import TravelWorker
from app.agents.workers.local_knowledge import LocalKnowledgeService, get_local_knowledge_service
from app.agents.workers.rag_analysis import analyze_worker_evidence, worker_result_from_analysis
from app.schemas.planning import ResearchTask, TravelRequirement, WorkerResult


class WeatherWorker(TravelWorker):
    def __init__(self, knowledge: LocalKnowledgeService | None = None, llm=None):
        self.knowledge = knowledge
        self.llm = llm

    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        query = f"{task.query} {requirement.departure_date} {requirement.days} days"
        evidence = (self.knowledge or get_local_knowledge_service()).search_destination(
            requirement.destination, "weather", query
        )
        analysis = await analyze_worker_evidence("weather", task, requirement, evidence, llm=self.llm)
        return worker_result_from_analysis(task, "weather", evidence, analysis)
