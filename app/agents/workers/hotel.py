from app.agents.workers.base import TravelWorker
from app.agents.workers.graph_knowledge import GraphKnowledgeService, get_graph_knowledge_service
from app.agents.workers.local_knowledge import LocalKnowledgeService, get_local_knowledge_service
from app.agents.workers.rag_analysis import analyze_worker_evidence, worker_result_from_analysis
from app.schemas.planning import ResearchTask, TravelRequirement, WorkerResult


class HotelWorker(TravelWorker):
    def __init__(
        self,
        knowledge: LocalKnowledgeService | None = None,
        llm=None,
        graph: GraphKnowledgeService | None = None,
    ):
        self.knowledge = knowledge
        self.llm = llm
        self.graph = graph

    async def run(self, task: ResearchTask, requirement: TravelRequirement) -> WorkerResult:
        query = f"{task.query} {' '.join(requirement.accommodation_preferences)}"
        document_evidence = (self.knowledge or get_local_knowledge_service()).search_destination(
            requirement.destination, "hotel", query
        )
        graph_evidence = await (self.graph or get_graph_knowledge_service()).search_related_entities(
            requirement.destination, "hotel", query
        )
        evidence = [*document_evidence, *graph_evidence]
        analysis = await analyze_worker_evidence("hotel", task, requirement, evidence, llm=self.llm)
        return worker_result_from_analysis(task, "hotel", evidence, analysis)
