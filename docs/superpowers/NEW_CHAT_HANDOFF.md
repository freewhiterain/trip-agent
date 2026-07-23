# Trip 项目新对话交接文档

> 给接手本项目的新 AI 使用。开始修改前先读完本文，再读 `.superpowers/sdd/progress.md`、阶段设计文档和当前 `git status`。

## 1. 用户目标与沟通背景

用户最初希望把项目改成参考图片的样子，但后来确认核心问题不是单纯的视觉样式，而是项目业务逻辑混乱。用户明确认可的目标是一个流程图式的旅行 Agent：

```text
用户消息
  -> Main Agent 判断本轮意图
      -> 明确规划旅行：展示三步旅行规划表单
      -> 目的地未确定但想要推荐：目的地推荐 RAG
      -> 开放式旅行问题：只用 RAG 回答
      -> 普通聊天：直接回答
  -> 用户确认表单
      -> Tool Result
      -> Supervisor
          -> attractions / weather / transport / hotel / food Workers
              -> RAG 检索证据
              -> Worker Agent 分析
          -> Supervisor 汇总
      -> 返回旅行草稿
```

用户的关键偏好：

- 直接在当前 main 工作方式上修改，不创建新分支，不主动 commit。
- 保留工作区中已有的用户修改、删除和未跟踪文件。
- 不使用 `git reset --hard`、`git checkout --` 或覆盖式恢复旧文件。
- 项目中很多旧内容不正确，必须先看实际逻辑和调用链，不能只按旧文件名猜测。
- 用户希望 Worker 是“RAG 检索 + Agent 思考后的结构化返回”，不是直接读取 Markdown，也不是直接把 Markdown 原文当答案。
- 当前数据可以先用简单的成都 Markdown 模拟资料，之后再单独设计真实数据源和新的 RAG 检索模式。
- 用户选择了成都作为当前阶段目的地。
- 用户曾要求能并发就并发，以缩短运行时间。
- 回答用户时使用简洁中文；较大修改前先用一两句话说明正在改什么。

## 2. 当前工作区与 Git 约束

- 实际项目目录：`D:\Desktop\project\Trip`
- 当前 Codex 工作目录通常是：`C:\Users\whiterain5\.codex\worktrees\daa3\Trip`
- 当前工作区可能是 detached HEAD，但用户要求按 main 的工作方式直接推进。
- 不要假设工作区干净。开始任务必须执行：

```powershell
git status --short --branch
```

- 不要提交 commit，除非用户明确重新要求。
- 不要丢弃已有变更。只修改本次任务需要的文件。
- 当前已有大量历史修改、删除和未跟踪文件，这不是本次 AI 可以自行清理的垃圾。

## 3. 已完成的旧阶段

此前已经完成主 Agent、表单工具、Tool Result 持久化、Supervisor 路由、前端三步表单和旧聊天逻辑清理等阶段。重要现状：

- Main Agent 会区分明确旅行规划、目的地推荐、开放式旅行问题和普通聊天。
- `collect_trip_requirements` 是唯一的旅行规划表单工具。
- 表单字段是 `destination`、`departure_date`、`days`，三者都必须由用户明确提交。
- 不允许系统自动补日期或天数，不允许把历史消息中的旧目的地强行套到当前请求。
- Tool Result 需要经过持久化和 exactly-once 保护，重复提交不能再次运行 Supervisor。
- Supervisor 只接收已确认的结构化需求，不负责决定是否弹出表单。
- 五个 Worker 名称固定为：
  - `attractions`：目的地景点，不是泛目的地研究
  - `weather`：天气
  - `transport`：交通
  - `hotel`：住宿
  - `food`：美食

## 4. 当前 Phase 2：本地模拟 RAG

阶段目标是先不接真实天气、地图、航班、铁路、酒店或餐厅 API，用成都 Markdown 验证完整链路：

```text
Markdown 模拟资料
  -> DocumentManager 加载和元数据补充
  -> ParentDocumentSplitter
  -> BM25 / Dense / RRF / rerank
  -> 按城市 + Worker 类别限定检索
  -> Worker Agent 结构化分析
  -> 证据 grounding
  -> Supervisor 汇总
  -> 模拟旅行草稿
```

重要原则：

- Markdown 只是知识库原料，不是 Worker 的直接输出。
- Worker 必须先调用类别限定检索，再把证据交给 `analyze_worker_evidence`。
- 没有证据时不能生成具体景点、价格、班次、库存、营业状态或天气事实。
- 本地 Markdown 结果必须带 `is_mock=True`，并保留来源文件和证据片段。
- LLM 没有配置或调用失败时，使用确定性证据摘要降级，不能阻塞 Supervisor。
- 没有资料时一般返回 `unavailable`；有部分证据但无法形成完整结论时使用 `partial`。
- 缺少交通出发地是既有输入不完整契约，`TransportWorker` 保持 `partial` 且不生成具体选项。

## 5. Phase 2 已完成任务

进度以 `.superpowers/sdd/progress.md` 为准，目前 Phase 2 Task 1 到 Task 6 已完成，且没有 commit。

### Task 1：成都模拟资料

已增加或处理：

- `data/documents/attractions/chengdu.md`
- `data/documents/weather/chengdu.md`
- `data/documents/transport/chengdu.md`
- `data/documents/accommodation/chengdu.md`
- `data/documents/food/chengdu.md`
- `app/rag/document_loader.py`

这些资料带有成都、类别和 `source_type=mock_markdown` 元数据。`accommodation` 目录映射为 Worker 类别 `hotel`。

### Task 2：类别限定 RAG

核心文件：

- `app/agents/workers/local_knowledge.py`
- `app/rag/document_loader.py`

核心接口：

```python
LocalKnowledgeService.search_destination(
    destination: str,
    category: TaskType,
    query: str,
) -> list[Evidence]
```

该接口先按城市和类别过滤，再进行检索；没有匹配资料时返回空列表，不允许回退到其他类别。

### Task 3：证据约束的 Worker Agent 分析

核心文件：

- `app/agents/workers/rag_analysis.py`
- `app/schemas/planning.py`

核心接口和结构：

```python
analyze_worker_evidence(
    worker,
    task,
    requirement,
    evidence,
    llm=None,
) -> WorkerAnalysis
```

`WorkerAnalysis` 包含 `summary`、`options`、`warnings`、`used_mock_data`。候选选项必须通过证据 grounding；没有证据支持的候选会被丢弃。

`WorkerResult` 现在支持：

- `WorkerStatus = completed | partial | unavailable | failed`
- `is_mock: bool = False`
- `evidence`
- `warnings`

### Task 4：五个 Worker 全部接入 RAG + Agent

已改造：

- `app/agents/workers/attractions.py`
- `app/agents/workers/weather.py`
- `app/agents/workers/transport.py`
- `app/agents/workers/hotel.py`
- `app/agents/workers/food.py`
- `app/agents/workers/registry.py`

每个 Worker 都按自己的类别调用 `search_destination`，然后调用 `analyze_worker_evidence`，最后通过统一映射生成 `WorkerResult`。

统一映射函数在 `app/agents/workers/rag_analysis.py`：

```python
worker_result_from_analysis(task, worker, evidence, analysis)
```

当前 Phase 2 默认只使用本地知识服务，不调用真实外部 API。外部适配器文件仍保留，后续阶段再重新设计接入。

### Task 5：Supervisor 保留结果

`app/agents/supervisor.py` 已经：

- 在 `assemble_draft` 中扁平化所有 Worker 证据。
- 合并并去重 Worker 警告。
- 保留每个 `WorkerResult` 的状态、`is_mock`、options、evidence 和 warnings。
- 在 `worker_completed` 事件中发送完整 WorkerResult。
- 证据存在时发送 `evidence_collected` 事件。

### Task 6：前端展示

`1_zhixing.html` 已增加：

- Worker 结果区域。
- `本地模拟资料` 标识。
- 每个 Worker 的状态和摘要。
- 可展开的证据来源和证据内容。
- 可展开的警告。
- 从 `assistant_result` 恢复历史结果。

相关测试位于：

- `tests/test_frontend_trip_form.py`
- `tests/test_phase2_rag_workers.py`

## 6. 最近验证结果

最近一次聚焦验证：

```text
tests/test_frontend_trip_form.py tests/test_phase2_rag_workers.py
21 passed, 3 warnings
```

此前还验证过：

```text
tests/test_phase2_rag_workers.py tests/test_phase1_supervisor.py
20 passed, 3 warnings

tests/test_phase2_rag_workers.py tests/test_phase1_supervisor.py tests/test_phase5_generate_first.py
25 passed, 3 warnings
```

警告主要来自依赖弃用和当前 worktree 无法写入 `.pytest_cache`，不是测试失败。

建议使用实际项目虚拟环境：

```powershell
$env:TEMP='C:\Windows\Temp'
$env:TMP='C:\Windows\Temp'
D:\Desktop\project\Trip\.venv\Scripts\python.exe -m pytest <focused-tests> -q
```

不要把“没有运行”写成“通过”。如果依赖或 Docker 不可用，明确记录 skipped/unavailable。

## 7. 下一步建议

下一步应完成 Phase 2 Task 7：端到端覆盖和文档收尾，重点包括：

1. 检查或创建 `tests/test_phase2_mock_rag_e2e.py`。
2. 覆盖完整流程：确认表单 -> Supervisor exactly once -> 五类类别限定检索 -> 五个 Worker 结果 -> 带证据的模拟草稿。
3. 覆盖缺少某类 Markdown 资料时的 `unavailable` 和其他 Worker 继续完成。
4. 覆盖 Worker Agent/LLM 失败时的确定性降级。
5. 检查 `README.md` 是否明确说明：当前仅成都、本地模拟 Markdown、`is_mock`、真实数据源和检索模式改造属于后续阶段。
6. 运行阶段 2 聚焦测试、相关 Supervisor/前端测试，再决定是否运行全量测试。
7. 完成验证后更新 `.superpowers/sdd/progress.md`，不要提交 commit。

## 8. 已知环境问题

- 之前启动 Docker 时 `travel_redis` 的 6379 端口映射失败，错误类似：
  `ports are not available: 0.0.0.0:6379`。
- Windows `Get-NetTCPConnection` 和 `netstat` 可能查不到占用，但 Docker 仍可能因端口排除范围或 Docker Desktop 状态失败。
- 之前 Redis 容器可能处于 `Exited (0)` 或启动时被删除，PostgreSQL 曾能正常运行。
- 启动项目时先检查 Docker 容器、端口和 `start.bat`，不要把 Docker 输出中的普通信息误认为应用错误。
- 当前测试环境使用 `D:\Desktop\project\Trip\.venv`，worktree 内可能没有 `.venv`。
- 当前工作区已有很多与本阶段无关的修改和删除，不能清理或重置。

## 9. 新对话第一步

新 AI 应按以下顺序执行：

1. 阅读本文件。
2. 阅读 `.superpowers/sdd/progress.md`。
3. 阅读 `docs/superpowers/specs/2026-07-23-trip-agent-phase2-mock-rag-design.md`。
4. 阅读 `docs/superpowers/plans/2026-07-23-trip-agent-phase2-mock-rag-implementation.md` 的 Task 7 及后续内容。
5. 执行 `git status --short --branch`，确认没有覆盖用户变更。
6. 检查当前 Task 7 是否已有部分实现和测试，避免重复创建文件。
7. 先写或补 focused test，再实现，再运行验证。
8. 只在用户明确要求时提交或上传 Git。

新 AI 的第一条回复可以简短说明：已读取交接文档，当前 Phase 2 Task 1-6 已完成，接下来检查并执行 Task 7；然后直接开始检查代码，不要重新询问已经确认过的业务流程。
