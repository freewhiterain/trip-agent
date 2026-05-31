"""
目的地 Router
并行查询探索 Agent 和天气 Agent
"""
from typing import TypedDict, Annotated, Literal
from operator import add
from pydantic import BaseModel, Field
from langchain_community.chat_models import ChatTongyi
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from app.config import settings
from app.utils.logger import app_logger


class Classification(TypedDict):
    agent: Literal["explore", "weather"]
    query: str


class AgentOutput(TypedDict):
    agent_name: str
    result: str


class DestinationRouterState(TypedDict):
    original_query: str
    destination: str
    classifications: list[Classification]
    agent_results: Annotated[list[AgentOutput], add]
    final_report: str


class ClassificationResult(BaseModel):
    classifications: list[Classification] = Field(
        description="要调用的 Agent 列表及其子查询"
    )


def classifier_node(state: DestinationRouterState) -> dict:
    """分类器节点：分析查询意图，决定调用哪些 Agent"""
    app_logger.info(f"分类器分析查询: {state['original_query']}")

    llm = ChatTongyi(model="qwen-max", api_key=settings.dashscope_api_key)
    structured_llm = llm.with_structured_output(ClassificationResult)

    result = structured_llm.invoke([
        {
            "role": "system",
            "content": """你是旅行查询分类专家。分析用户查询，决定需要调用哪些 Agent：

**可用 Agent**：
- explore：景点攻略、美食推荐、住宿信息（从知识库检索）
- weather：实时天气信息（调用天气 API）

**分类规则**：
1. 涉及景点、美食、住宿、攻略 → 调用 explore
2. 涉及天气、气温、降雨 → 调用 weather
3. 综合性查询（如"推荐XX旅游"）→ 调用两个

返回 JSON，包含 classifications 列表，每项：agent 和 query。"""
        },
        {
            "role": "user",
            "content": f"目的地：{state['destination']}\n查询：{state['original_query']}"
        }
    ])

    app_logger.info(f"✅ 分类完成：{len(result.classifications)} 个 Agent")
    return {"classifications": result.classifications}


def route_to_agents(state: DestinationRouterState) -> list[Send]:
    """路由函数：根据分类结果并行发送任务"""
    sends = []
    for classification in state["classifications"]:
        sends.append(Send(
            classification["agent"],
            {"query": classification["query"], "destination": state["destination"]}
        ))
    app_logger.info(f"并行发送 {len(sends)} 个任务")
    return sends


def explore_agent_node(state: dict) -> dict:
    """探索 Agent：从 RAG 检索景点攻略"""
    query = state["query"]
    destination = state["destination"]
    app_logger.info(f"探索 Agent 执行: {query}")

    # TODO: 第六章完善后，实际调用 RAG 检索
    result = f"""## {destination} 景点攻略

### 必游景点
1. {destination}核心景区（详细攻略待 RAG 接入）
2. 历史文化景点
3. 自然风景区

### 推荐行程
Day 1: 主要景区
Day 2: 文化探索

（实际会从知识库检索详细攻略）
"""
    return {"agent_results": [{"agent_name": "explore", "result": result}]}


def weather_agent_node(state: dict) -> dict:
    """天气 Agent：查询天气信息"""
    destination = state["destination"]
    app_logger.info(f"天气 Agent 执行: {destination}")

    # TODO: 第七章接入高德天气 MCP 后实现
    result = f"""## {destination} 天气信息

今日：晴，温度适宜
未来三天：以晴为主，适合出行

（实际会调用高德天气 API）
"""
    return {"agent_results": [{"agent_name": "weather", "result": result}]}


def synthesizer_node(state: DestinationRouterState) -> dict:
    """综合器节点：合并多个 Agent 的结果"""
    app_logger.info("综合 Agent 结果...")
    results = state["agent_results"]

    if not results:
        return {"final_report": "未找到相关信息。"}

    sections = []
    for agent_output in results:
        sections.append(f"**来自 {agent_output['agent_name']}：**\n{agent_output['result']}")

    final_report = "\n\n".join(sections)
    app_logger.info("✅ 综合完成")
    return {"final_report": final_report}


def create_destination_router():
    """创建目的地 Router"""
    workflow = StateGraph(DestinationRouterState)

    workflow.add_node("classifier", classifier_node)
    workflow.add_node("explore", explore_agent_node)
    workflow.add_node("weather", weather_agent_node)
    workflow.add_node("synthesizer", synthesizer_node)

    workflow.add_edge(START, "classifier")
    workflow.add_conditional_edges("classifier", route_to_agents, ["explore", "weather"])
    workflow.add_edge("explore", "synthesizer")
    workflow.add_edge("weather", "synthesizer")
    workflow.add_edge("synthesizer", END)

    app = workflow.compile()
    app_logger.info("✅ 目的地 Router 创建完成")
    return app
