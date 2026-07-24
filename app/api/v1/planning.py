"""任务、事件、审批、偏好和正式行程 API。"""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.supervisor import run_travel_planning
from app.api.dependencies import get_current_user
from app.core.checkpointer import get_checkpointer
from app.governance.approvals import ApprovalService
from app.governance.events import TaskEventService
from app.governance.itineraries import ItineraryGovernanceService
from app.governance.postgres import (
    PostgresApprovalRepository,
    PostgresEventRepository,
    PostgresItineraryRepository,
    PostgresPreferenceRepository,
    PostgresTripHistoryRepository,
)
from app.memory.defaults import apply_preference_defaults, resolve_preference_defaults
from app.memory.service import MemoryGovernanceService
from app.models.user import User
from app.schemas.governance import ApprovalDecisionRequest, ItinerarySaveRequest, PreferenceProposalRequest
from app.schemas.planning import TravelRequirement
from app.utils.logger import app_logger

router = APIRouter(tags=["旅行规划任务"])


@router.post("/tasks")
async def create_planning_task(requirement: TravelRequirement, user: User = Depends(get_current_user)):
    task_id = uuid4().hex
    event_service = TaskEventService(PostgresEventRepository())
    try:
        defaults = await resolve_preference_defaults(str(user.id), PostgresPreferenceRepository())
    except Exception as exc:
        app_logger.warning(f"读取长期偏好失败，按无偏好处理: task_id={task_id} error={exc}", exc_info=True)
        defaults = {}
    requirement = apply_preference_defaults(requirement, defaults)
    draft = await run_travel_planning(
        requirement,
        checkpointer=await get_checkpointer(),
        event_service=event_service,
        task_id=task_id,
        user_id=str(user.id),
    )
    return {"task_id": task_id, "status": "completed", "draft": draft.model_dump(mode="json")}


@router.get("/tasks/{task_id}")
async def get_planning_task(task_id: str, user: User = Depends(get_current_user)):
    events = await PostgresEventRepository().list(task_id, str(user.id))
    if not events:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="任务不存在")
    return {
        "task_id": task_id,
        "status": "completed" if events[-1].event_type == "task_completed" else "running",
        "last_event": events[-1].model_dump(mode="json"),
    }


@router.get("/tasks/{task_id}/events")
async def get_task_events(task_id: str, user: User = Depends(get_current_user)):
    events = await PostgresEventRepository().list(task_id, str(user.id))
    return {"task_id": task_id, "events": [event.model_dump(mode="json") for event in events]}


@router.post("/preferences/proposals")
async def propose_preference(data: PreferenceProposalRequest, user: User = Depends(get_current_user)):
    service = MemoryGovernanceService(
        ApprovalService(PostgresApprovalRepository()),
        PostgresPreferenceRepository(),
    )
    approval = await service.request_upsert(data.task_id, str(user.id), data.key, data.value)
    return approval.model_dump(mode="json")


@router.post("/itineraries/{conversation_id}/save")
async def request_itinerary_save(
    conversation_id: str,
    data: ItinerarySaveRequest,
    user: User = Depends(get_current_user),
):
    service = ItineraryGovernanceService(
        ApprovalService(PostgresApprovalRepository()),
        PostgresItineraryRepository(),
    )
    approval = await service.request_save(
        data.task_id, str(user.id), conversation_id, data.title, data.content
    )
    return approval.model_dump(mode="json")


@router.get("/itineraries/{conversation_id}")
async def get_saved_itinerary(conversation_id: str, user: User = Depends(get_current_user)):
    record = await PostgresItineraryRepository().get(str(user.id), conversation_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="正式行程不存在")
    return record


@router.post("/approvals/{approval_id}/decision")
async def decide_approval(
    approval_id: str,
    data: ApprovalDecisionRequest,
    user: User = Depends(get_current_user),
):
    approval_repository = PostgresApprovalRepository()
    approvals = ApprovalService(approval_repository)
    try:
        record = await approvals.decide(approval_id, str(user.id), data.decision, data.payload)
        applied = None
        if record.status in {"approved", "edited"} and record.action.startswith("memory."):
            applied = await MemoryGovernanceService(
                approvals, PostgresPreferenceRepository()
            ).apply(record.id, str(user.id))
        elif record.status in {"approved", "edited"} and record.action.startswith("itinerary."):
            applied = await ItineraryGovernanceService(
                approvals, PostgresItineraryRepository(), PostgresTripHistoryRepository()
            ).apply(record.id, str(user.id))
        return {
            "approval": record.model_dump(mode="json"),
            "applied": applied.model_dump(mode="json") if hasattr(applied, "model_dump") else applied,
        }
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
