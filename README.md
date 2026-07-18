# 知行旅行助手

基于 LangGraph 的企业级智能旅行规划系统。

## 快速启动

### 第一步：配置环境变量

编辑 `.env` 文件，填入必要的 API Key：

```
DASHSCOPE_API_KEY=sk-你的千问APIKey   # 必填
LANGSMITH_API_KEY=你的LangSmithKey    # 推荐
AMAP_API_KEY=你的高德Key              # 第7章需要
TAVILY_API_KEY=你的TavilyKey          # 第7章需要
```

DashScope 注册：https://dashscope.aliyun.com/

### 第二步：启动 Docker 数据库

```powershell
# PostgreSQL (pgvector)
docker run -d `
  --name travel_postgres `
  -e POSTGRES_DB=ai_travel_db `
  -e POSTGRES_USER=travel_user `
  -e POSTGRES_PASSWORD=travel123456 `
  -p 5432:5432 `
  --restart unless-stopped `
  pgvector/pgvector:pg17

# Redis
docker run -d `
  --name travel_redis `
  -p 6379:6379 `
  --restart unless-stopped `
  redis:7-alpine redis-server --appendonly yes
```

### 第三步：初始化数据库

```powershell
python scripts/init_db.py
```

### 第四步：测试 LLM 连接

```powershell
python scripts/test_llm.py
```

### 第五步：运行对话测试

```powershell
python tests/handoffs_flow_test.py
```

### 第六步：初始化 RAG（可选，需要 DashScope Key）

```powershell
python scripts/init_rag.py
```

### 第七步：启动 FastAPI 服务

```powershell
python app/main.py
# 访问 http://localhost:8000/docs
```

## 项目结构

```
app/
├── core/          # 状态、Checkpointer、Store、中间件
├── agents/        # Handoffs主流程、Router、Subagents
├── tools/         # 状态转换工具、回退工具
├── rag/           # 混合检索、向量存储
├── mcp_core/      # MCP 服务器和客户端
├── api/           # FastAPI 路由
├── models/        # SQLAlchemy ORM 模型
└── schemas/       # Pydantic 请求/响应模型
scripts/           # 初始化和测试脚本
tests/             # 测试文件
data/documents/    # RAG 文档库（放 .md 攻略文件）
```

## 技术栈

LangGraph 1.0 · LangChain 1.0 · FastAPI · PostgreSQL 17 + pgvector · Redis · ChromaDB · Qwen-Max · SSE

## 当前首版实现状态

首版默认使用 `TRAVEL_AGENT_MODE=supervisor`，提供只读旅行规划与推荐：

- Supervisor + Planner-Worker，并行执行目的地、交通、住宿、美食和天气研究。
- Hybrid RAG：稳定文档/切片 ID、BM25、Dense、RRF、相关性重排和父文档回溯。
- Evidence：来源、URL、查询时间、有效期、置信度和冲突检查。
- MCP/API 可靠性：超时、有限重试、请求去重、TTL 缓存、熔断和明确降级。
- LangGraph Checkpointer、任务事件、Interrupt 审批和用户隔离。
- 用户明确批准后才写入长期偏好或覆盖正式行程。
- 首版不提供购票、预订、支付、退款、取消、改签或外部消息发送。

没有配置实时数据源时，系统会明确说明数据不可用，不生成虚构班次、价格、库存或天气。

## 关键配置

复制 `.env.example` 为 `.env`，至少配置独立的 `JWT_SECRET_KEY` 和 `DASHSCOPE_API_KEY`。实时数据需要显式设置 `ENABLE_EXTERNAL_TOOLS=true`，并配置相应的 `AMAP_API_KEY`、`TAVILY_API_KEY` 或 MCP 服务地址。

## 主要接口

- `POST /api/v1/chat/stream/{conversation_id}`：兼容旧客户端的 SSE 对话。
- `POST /api/v1/tasks`：提交结构化旅行需求并执行 Supervisor 规划。
- `GET /api/v1/tasks/{task_id}`：查询任务状态。
- `GET /api/v1/tasks/{task_id}/events`：读取持久化任务事件。
- `POST /api/v1/approvals/{approval_id}/decision`：提交 `approve`、`edit` 或 `reject`。
- `POST /api/v1/preferences/proposals`：提出长期偏好写入申请。
- `POST /api/v1/itineraries/{conversation_id}/save`：提出正式行程保存/覆盖申请。

## 测试

```powershell
\.venv\Scripts\python.exe -m pytest -q
```

默认会跳过需要真实模型和网络的集成测试；设置 `RUN_EXTERNAL_TESTS=1` 后再运行这些测试。启动服务前先执行 `python scripts/init_db.py` 初始化业务表、Checkpointer、Store 和 pgvector 扩展。
