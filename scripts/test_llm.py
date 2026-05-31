"""
测试千问 LLM 连接
（按课件第一章 4.6 节实现）
"""
# === 兼容性修复：让脚本能直接运行（python scripts/test_llm.py） ===
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# === 修复结束 ===

from dotenv import load_dotenv
from langchain_community.chat_models import ChatTongyi
from langchain_core.messages import HumanMessage

load_dotenv()


def test_qwen_connection():
    """测试千问模型连接"""

    print("测试千问模型连接...")

    try:
        # 初始化模型
        model = ChatTongyi(
            model="qwen-max",
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            temperature=0.7
        )

        # 发送测试消息
        response = model.invoke([
            HumanMessage(content="你好，请用一句话介绍你自己。")
        ])

        print(f"✅ 连接成功！模型回复：\n{response.content}")

    except Exception as e:
        print(f"❌ 连接失败：{e}")
        raise


if __name__ == "__main__":
    test_qwen_connection()
