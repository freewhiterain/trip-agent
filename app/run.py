"""
启动脚本 - Windows 兼容版本（强制 SelectorEventLoop）

使用方法：
    python app/run.py
"""
import sys
import os
import asyncio
import selectors

# === 兼容性修复（课件没有）：让脚本能直接运行 ===
# app/run.py 在 app/ 目录里，需要把项目根目录加入 sys.path 才能导入 app 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# === 修复结束 ===

# 必须在导入任何其他模块之前设置！
if sys.platform == "win32":
    # 创建 selector
    selector = selectors.SelectSelector()
    # 创建基于 selector 的事件循环
    loop = asyncio.SelectorEventLoop(selector)
    # 设置为当前事件循环
    asyncio.set_event_loop(loop)
    # 设置策略（防止后续代码创建新的 ProactorEventLoop）
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    # 关键：使用 uvicorn.Server 手动运行，而不是 uvicorn.run()
    config = uvicorn.Config(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        loop="none",  # 关键！告诉 uvicorn 不要创建新的事件循环
    )
    server = uvicorn.Server(config)

    # 使用我们创建的 SelectorEventLoop 运行（兼容 Python 3.13）
    if sys.platform == "win32":
        loop.run_until_complete(server.serve())
    else:
        asyncio.run(server.serve())
