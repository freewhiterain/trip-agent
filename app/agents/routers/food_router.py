"""
餐饮 Router
根据餐饮类型并行查询不同信息源
"""
from typing import TypedDict, Annotated, Literal
from operator import add
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from app.utils.logger import app_logger
from app.agents.workers.local_knowledge import load_destination_evidence


class FoodRouterState(TypedDict):
    destination: str
    food_types: list[str]
    query_results: Annotated[list[dict], add]
    final_recommendations: str


def route_by_food_type(state: FoodRouterState) -> list[Send]:
    sends = []
    for food_type in state["food_types"]:
        sends.append(Send(
            "food_query",
            {"destination": state["destination"], "food_type": food_type}
        ))
    return sends


def food_query_node(state: dict) -> dict:
    destination = state["destination"]
    food_type = state["food_type"]
    app_logger.info(f"查询餐饮: {destination} - {food_type}")

    evidence = load_destination_evidence(destination, f"food {food_type}")
    if not evidence:
        result = f"{destination} 暂无可验证的 {food_type} 餐饮资料，未生成具体商家或价格。"
    else:
        result = "\n".join(f"来源：{item.source}\n{item.content[:500]}" for item in evidence)
    return {"query_results": [{"food_type": food_type, "result": result}]}


def synthesize_food(state: FoodRouterState) -> dict:
    results = state["query_results"]
    parts = [r["result"] for r in results]
    return {"final_recommendations": "\n\n".join(parts)}


def create_food_router():
    workflow = StateGraph(FoodRouterState)
    workflow.add_node("food_query", food_query_node)
    workflow.add_node("synthesize", synthesize_food)
    workflow.add_conditional_edges(START, route_by_food_type, ["food_query"])
    workflow.add_edge("food_query", "synthesize")
    workflow.add_edge("synthesize", END)
    return workflow.compile()
