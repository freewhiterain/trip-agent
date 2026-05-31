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
