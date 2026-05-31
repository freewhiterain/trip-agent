"""
Handoffs 主流程交互测试
运行方式：python tests/handoffs_flow_test.py
"""
import asyncio
import sys
import os
import uuid

# === 兼容性修复（课件没有）：让脚本能直接运行 ===
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# === 修复结束 ===

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from app.agents.handoffs.travel_agent import create_travel_agent


async def run_interactive_chat():
    """运行持续对话的测试循环"""
    travel_agent = await create_travel_agent()

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    print(f"=== 开始测试 (会话 ID: {thread_id}) ===")
    print("输入 'q', 'quit', 'exit' 退出对话")
    print("-" * 50)

    while True:
        try:
            user_input = input("\nUser: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["q", "quit", "exit"]:
                print("结束对话。")
                break

            inputs = {"messages": [HumanMessage(content=user_input)]}
            print("\nAssistant: ", end="", flush=True)

            last_message_id = None
            async for event in travel_agent.astream(inputs, config=config, stream_mode="values"):
                messages = event.get("messages", [])
                if messages:
                    last_msg = messages[-1]
                    if last_msg.id == last_message_id:
                        continue
                    last_message_id = last_msg.id

                    if isinstance(last_msg, AIMessage) and last_msg.content:
                        print(last_msg.content)
                    elif isinstance(last_msg, ToolMessage):
                        print(f"\n[工具执行] {last_msg.name}: {last_msg.content[:100]}")

        except KeyboardInterrupt:
            print("\n用户中断。")
            break
        except Exception as e:
            print(f"\n❌ 发生错误: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(run_interactive_chat())
