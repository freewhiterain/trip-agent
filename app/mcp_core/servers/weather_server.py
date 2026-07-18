"""高德天气 MCP Server；返回结构化 Evidence。"""

from fastmcp import FastMCP

from app.mcp_core.adapters.weather import AmapWeatherAdapter
from app.mcp_core.reliability import ExternalServiceError

mcp = FastMCP("天气服务")
adapter = AmapWeatherAdapter()


@mcp.tool()
async def get_weather(city: str, forecast: bool = True) -> dict:
    """查询国内城市天气，返回 Evidence 列表或明确错误。"""
    try:
        evidence = await adapter.query(city, forecast)
        return {
            "status": "completed" if evidence else "partial",
            "evidence": [item.model_dump(mode="json") for item in evidence],
            "warnings": [] if evidence else ["天气服务未返回结果。"],
        }
    except ExternalServiceError as exc:
        return {"status": "failed", "evidence": [], "warnings": [str(exc)]}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=9001)
