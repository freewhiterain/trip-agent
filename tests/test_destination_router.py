"""
测试目的地 Router
运行方式：python tests/test_destination_router.py
"""
import asyncio
import sys
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_EXTERNAL_TESTS") != "1",
    reason="设置 RUN_EXTERNAL_TESTS=1 后运行真实模型集成测试",
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.agents.routers.destination_router import create_destination_router


async def test_explore_only():
    router = create_destination_router()
    result = await router.ainvoke({
        "original_query": "西安有什么好玩的景点？",
        "destination": "西安"
    })
    print("\n=== 景点查询 ===")
    print(f"分类：{result['classifications']}")
    print(f"报告：\n{result['final_report']}")


async def test_weather_only():
    router = create_destination_router()
    result = await router.ainvoke({
        "original_query": "西安现在天气怎么样？",
        "destination": "西安"
    })
    print("\n=== 天气查询 ===")
    print(f"分类：{result['classifications']}")
    print(f"报告：\n{result['final_report']}")


async def test_both_agents():
    router = create_destination_router()
    result = await router.ainvoke({
        "original_query": "推荐西安旅游",
        "destination": "西安"
    })
    print("\n=== 综合查询 ===")
    print(f"分类：{result['classifications']}")
    print(f"报告：\n{result['final_report']}")


if __name__ == "__main__":
    asyncio.run(test_explore_only())
    asyncio.run(test_weather_only())
    asyncio.run(test_both_agents())
