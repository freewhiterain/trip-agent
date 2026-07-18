"""Worker 公共接口。"""

from abc import ABC, abstractmethod

from app.schemas.planning import ResearchTask, TravelRequirement, WorkerResult


class TravelWorker(ABC):
    @abstractmethod
    async def run(
        self,
        task: ResearchTask,
        requirement: TravelRequirement,
    ) -> WorkerResult:
        """执行一个只读研究任务。"""
