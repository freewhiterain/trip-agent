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
  -p 15432:5432 `
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
python -m pytest -q
```

### 第六步：初始化 RAG（可选，需要 DashScope Key）

```powershell
python scripts/init_rag.py
```

### 第七步：启动 FastAPI 服务

```powershell
python app/main.py
# 访问 http://localhost:18000/docs
```

## 项目结构

```
app/
├── core/          # Checkpointer、Store
├── agents/        # 对话协调器、Supervisor 图、Planner、Workers
├── governance/    # 事件、审批、偏好、行程草稿仓库
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

## 当前实现状态

每轮消息先由 Main Agent 路由：旅行规划进入表单 Tool，开放旅行问题进入 RAG，其余消息直接对话。新会话会主动询问是否需要规划旅行。

- 表单通过 Tool Call / Tool Result 交互，必须收集目的地、出发日期和旅行天数，不使用默认日期或默认天数。
- 目的地未定时可暂停表单并请求 RAG 推荐；选择城市后恢复原表单状态。
- 只有三项必填字段完整后才调用 Supervisor。Supervisor 调度景点、天气、交通、住宿和美食五类 Worker。
- Worker 的实时数据源设计暂缓；未配置可用数据源时必须明确降级，不生成虚构事实。
- 行程草稿常驻（`models/draft.py` + `governance/drafts.py`）：每个会话一份可增量编辑的草稿，版本递增；正式行程仍需审批落库。
- 行程草稿常驻（`models/draft.py` + `governance/drafts.py`）：每个会话一份可增量编辑的草稿，版本递增；正式行程仍需审批落库。
- Hybrid RAG：稳定文档/切片 ID、BM25、Dense、RRF、相关性重排和父文档回溯。
- Evidence：来源、URL、查询时间、有效期、置信度和冲突检查。
- MCP/API 可靠性：超时、有限重试、请求去重、TTL 缓存、熔断和明确降级。
- LangGraph Checkpointer、任务事件持久化（含 task_failed）和用户隔离。
- 用户明确批准后才写入长期偏好或覆盖正式行程。
- 不提供购票、预订、支付、退款、取消、改签或外部消息发送。

没有配置实时数据源时，系统会明确说明数据不可用，不生成虚构班次、价格、库存或天气。旧 Handoffs 状态机与交通 Subagents 已退役。

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
