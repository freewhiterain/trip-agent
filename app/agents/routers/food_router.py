"""
餐饮 Router
根据餐饮类型并行查询不同信息源
"""
from typing import TypedDict, Annotated, Literal
from operator import add
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from app.utils.logger import app_logger


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

    # TODO: 接入 RAG 检索餐饮文档
    return {"query_results": [{"food_type": food_type, "result": f"{destination} {food_type} 推荐（待 RAG 接入）"}]}


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
