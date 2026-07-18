"""LangGraph Interrupt 驱动的可恢复审批流程。"""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class ApprovalWorkflowState(TypedDict, total=False):
    approval_id: str
    user_id: str
    action: str
    payload: dict[str, Any]
    status: str
    decision: str
    decision_payload: dict[str, Any] | None


def create_approval_workflow(checkpointer):
    def wait_for_approval(state: ApprovalWorkflowState):
        response = interrupt(
            {
                "type": "approval",
                "approval_id": state["approval_id"],
                "action": state["action"],
                "payload": state["payload"],
                "allowed_decisions": ["approve", "edit", "reject"],
            }
        )
        decision = response.get("decision")
        if decision not in {"approve", "edit", "reject"}:
            raise ValueError("无效的审批决定")
        return {
            "decision": decision,
            "decision_payload": response.get("payload"),
            "status": {"approve": "approved", "edit": "edited", "reject": "rejected"}[decision],
        }

    workflow = StateGraph(ApprovalWorkflowState)
    workflow.add_node("wait_for_approval", wait_for_approval)
    workflow.add_edge(START, "wait_for_approval")
    workflow.add_edge("wait_for_approval", END)
    return workflow.compile(checkpointer=checkpointer)
