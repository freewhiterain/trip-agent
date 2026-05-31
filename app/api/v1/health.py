"""
健康检查 API
"""
from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["健康检查"])


@router.get("")
async def health_check():
    """基础健康检查"""
    return {"status": "healthy", "service": "LangGraph Travel Planner"}


@router.get("/detail")
async def health_detail():
    """详细健康检查"""
    from app.core.checkpointer import get_checkpointer
    from app.core.store import get_store

    status = {"checkpointer": "unknown", "store": "unknown"}

    try:
        await get_checkpointer()
        status["checkpointer"] = "ready"
    except Exception as e:
        status["checkpointer"] = f"error: {e}"

    try:
        await get_store()
        status["store"] = "ready"
    except Exception as e:
        status["store"] = f"error: {e}"

    all_ready = all(v == "ready" for v in status.values())
    return {"status": "healthy" if all_ready else "degraded", "components": status}
