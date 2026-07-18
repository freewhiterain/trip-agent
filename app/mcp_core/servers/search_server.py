"""Tavily 搜索 MCP Server；返回结构化 Evidence。"""

from fastmcp import FastMCP

from app.mcp_core.adapters.search import TavilySearchAdapter
from app.mcp_core.reliability import ExternalServiceError

mcp = FastMCP("搜索服务")
adapter = TavilySearchAdapter()


@mcp.tool()
async def web_search(query: str, max_results: int = 5) -> dict:
    """执行只读网络搜索，返回带来源与有效期的 Evidence。"""
    try:
        evidence = await adapter.search(query, max_results)
        return {
            "status": "completed" if evidence else "partial",
            "evidence": [item.model_dump(mode="json") for item in evidence],
            "warnings": [] if evidence else ["未找到相关结果。"],
        }
    except ExternalServiceError as exc:
        return {"status": "failed", "evidence": [], "warnings": [str(exc)]}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=9002)
