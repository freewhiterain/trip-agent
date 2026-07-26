"""
FastAPI 应用入口
"""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from app.agents.factory import create_planning_registry
from app.config import settings
from app.governance.drafts import PostgresDraftRepository
from app.utils.logger import app_logger
from app.api.v1 import conversations, chat, planning, tools, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    import asyncio

    settings.validate_security()

    loop = asyncio.get_running_loop()
    app_logger.info(f"FastAPI 使用的事件循环: {type(loop).__name__}")

    from app.core.checkpointer import checkpointer_lifespan
    from app.mcp_core.client import MCPClientManager
    from app.core.store import store_lifespan

    app_logger.info("启动应用...")

    async with checkpointer_lifespan():
        app_logger.info("Checkpointer 已就绪")

        async with store_lifespan():
            app_logger.info("Store 已就绪")

            # 初始化 MCP（如果配置了的话）
            mcp = await MCPClientManager.get_instance()
            planning_registry, planning_fallback_reason = create_planning_registry()
            app.state.planning_registry = planning_registry
            app.state.planning_fallback_reason = planning_fallback_reason
            app.state.draft_repository = PostgresDraftRepository()
            app_logger.info("MCP 服务初始化完成")

            yield

            # 关闭 MCP
            try:
                if hasattr(mcp, "close"):
                    await mcp.close()
                app_logger.info("MCP 服务已关闭")
            except Exception as e:
                app_logger.warning(f"MCP 关闭异常: {e}")

    app_logger.info("应用已关闭")


app = FastAPI(
    title="LangGraph 旅行规划系统",
    description="企业级多 Agent 旅行规划服务",
    version="1.0.0",
    lifespan=lifespan
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


@app.get("/ui", include_in_schema=False)
async def ui():
    return FileResponse(_PROJECT_ROOT / "1_zhixing.html")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API 路由
app.include_router(users.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(tools.router, prefix="/api/v1")
app.include_router(planning.router, prefix="/api/v1")


@app.get("/")
async def root():
    return {
        "status": "healthy",
        "service": "LangGraph Travel Planner",
        "version": "1.0.0",
        "docs": "/docs"
    }
