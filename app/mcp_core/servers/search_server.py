"""
搜索服务 MCP Server
使用 Tavily 搜索 API

启动方式：python app/mcp_core/servers/search_server.py
"""
import os
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("搜索服务")

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")


@mcp.tool()
async def web_search(query: str, max_results: int = 5) -> str:
    """
    网络搜索

    参数：
    - query: 搜索关键词
    - max_results: 最大结果数（默认 5）

    返回：
    - 搜索结果摘要
    """
    if not TAVILY_API_KEY:
        return "❌ Tavily API Key 未配置，请在 .env 文件中设置 TAVILY_API_KEY"

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=TAVILY_API_KEY)
        results = client.search(query=query, max_results=max_results)

        lines = [f"## 搜索结果：{query}\n"]
        for i, result in enumerate(results.get("results", []), 1):
            lines.append(
                f"{i}. **{result.get('title', '无标题')}**\n"
                f"   {result.get('content', '')[:200]}...\n"
                f"   来源：{result.get('url', '')}\n"
            )

        return "\n".join(lines) if len(lines) > 1 else "未找到相关结果"

    except ImportError:
        return "❌ 请安装 tavily-python：pip install tavily-python"
    except Exception as e:
        return f"❌ 搜索失败：{e}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=9002)
