"""
测试交通规划 Subagents 系统
运行方式：python tests/test_transport_subagents.py
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

from app.agents.subagents.transport_coordinator import create_transport_coordinator


async def test_flight_query():
    coordinator = create_transport_coordinator()
    response = await coordinator.ainvoke({
        "messages": [{"role": "user", "content": "我想从北京飞到上海，8月1日出发，2个人，请帮我查询航班。"}]
    })
    print(f"\n=== 航班查询 ===\n{response['messages'][-1].content}")


async def test_train_query():
    coordinator = create_transport_coordinator()
    response = await coordinator.ainvoke({
        "messages": [{"role": "user", "content": "北京到西安，8月1日，坐高铁，帮我查一下车次。"}]
    })
    print(f"\n=== 高铁查询 ===\n{response['messages'][-1].content}")


async def test_driving_route():
    coordinator = create_transport_coordinator()
    response = await coordinator.ainvoke({
        "messages": [{"role": "user", "content": "我打算自驾从北京到上海，帮我规划一下路线。"}]
    })
    print(f"\n=== 自驾路线 ===\n{response['messages'][-1].content}")


if __name__ == "__main__":
    asyncio.run(test_flight_query())
    asyncio.run(test_train_query())
    asyncio.run(test_driving_route())
