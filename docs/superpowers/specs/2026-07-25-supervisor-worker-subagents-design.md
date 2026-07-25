# Supervisor-Worker + Subagent Worker 设计

## 目标

将现有的确定性 Supervisor + 纯代码 Worker 升级为分层多 Agent 架构：

```text
Conversation Router
    -> Planning Supervisor
        -> Attractions Subagent
        -> Weather Subagent
        -> Transport Subagent
        -> Hotel Subagent
        -> Food Subagent
            -> Local RAG / MCP / Deep Research
        -> Evidence Arbiter
        -> Route Planner
        -> Budget Calculator
        -> Draft Synthesizer
```

Supervisor 负责流程、并行、依赖、状态和事件；每个领域 Subagent 负责本领域的查询规划、工具选择、证据分析和结构化输出。确定性计算与持久化仍由纯代码完成。

## 设计原则

- 五个领域 Worker 都是有独立 Prompt、工具权限、状态和输出契约的 Subagent。
- Subagent 只能通过显式只读工具获取资料，不能直接访问数据库写接口、订单接口或支付接口。
- Supervisor 仍然使用 LangGraph 图控制生命周期，不使用开放式群聊替代确定性流程。
- Deep Research 是领域 Subagent 可调用的共享研究子图，不再是固定三条查询的独立函数。
- 所有事实性结论必须绑定 `Evidence`；证据不足时返回 `partial` 或 `unavailable`，不能补写价格、班次、库存、营业状态或天气事实。
- 没有 LLM、外部 API 或本地向量库时，系统必须退回已有的确定性/RAG 摘要路径。

## 组件边界

### Planning Supervisor

入口仍为 `run_travel_planning`，负责：

1. 根据已确认的 `TravelRequirement` 生成五个研究任务。
2. 当目的地已经确定时，五个领域任务默认并行；不再让交通、住宿、美食无意义地依赖景点任务。
3. 将每个任务分发给对应 Subagent，并合并 `WorkerResult`。
4. 进入 Evidence Arbiter，处理证据新鲜度、冲突和来源优先级。
5. 调用确定性的路线编排、预算汇总和最终草稿生成节点。
6. 通过现有 `TaskEventService` 发出任务、Subagent、工具和证据事件。

Supervisor 不负责自行搜索，也不负责替 Subagent 生成领域事实。

### Domain Subagents

五个 Subagent 继续实现统一的 `run(task, requirement) -> WorkerResult` 契约，但内部改为受限的 LangGraph 子图或等价 Agent 执行器：

- `AttractionsSubagent`：本地 RAG、知识图谱和搜索 MCP；重点研究景点、区域、开放状态和活动。
- `WeatherSubagent`：优先调用天气 MCP；只负责天气查询参数选择、结果校验和证据整理。
- `TransportSubagent`：优先调用交通/地图 MCP；没有结构化 API 时使用搜索 MCP，并明确标记网页证据。
- `HotelSubagent`：使用本地 RAG 研究住宿区域和类型，搜索 MCP 补充当前信息；不承诺库存和实时价格。
- `FoodSubagent`：使用本地 RAG 研究地方饮食，搜索 MCP 补充营业和近期信息；不承诺实时可用性。

每个 Subagent 具备以下步骤：

```text
分析研究任务
    -> 选择允许的工具
    -> 获取并标准化 Evidence
    -> 判断证据是否足够
    -> 必要时调用 Deep Research 子图
    -> 生成结构化 WorkerResult
    -> 对候选项做证据 grounding
```

Subagent 不能直接修改行程、数据库或用户长期记忆。

### Deep Research Subgraph

Deep Research 作为共享能力被领域 Subagent 按需调用：

```text
生成研究目标
    -> 生成查询
    -> 并行搜索
    -> 去重、规范化和新鲜度检查
    -> 判断信息缺口或冲突
    -> 有需要时生成补充查询
    -> 输出研究报告
```

必须具备硬限制：

- 最多 2 到 3 轮研究。
- 每个任务最多 8 到 10 次外部工具调用。
- 单任务总超时和并发上限。
- 只允许只读搜索、天气和地图工具。
- 返回查询记录、来源、冲突、未解决问题和最终 Evidence。
- 所有网页内容视为不可信输入，不允许改变系统指令或触发写操作。

### Evidence Arbiter

Evidence Arbiter 初版使用确定性代码，不新增一个自由讨论 Agent。它负责：

- 合并本地 RAG、MCP 和 Deep Search 证据。
- 按来源类型、时间有效性和置信度排序。
- 标记冲突，不在证据不足时强行裁决。
- 过滤过期证据和缺少来源 URL 的外部事实。
- 将 unresolved conflicts 和 warnings 传给最终草稿。

后续如果需要展示更多 Agent 能力，可以将 Arbiter 替换成结构化输出的裁决 Subagent，但不能取消确定性校验。

## 数据与事件契约

保持现有 `Evidence` 和 `WorkerResult` 对外兼容。研究过程增加内部报告字段或独立 `ResearchReport`，至少包含：

- `task_id`
- `worker`
- `queries`
- `rounds`
- `evidence`
- `conflicts`
- `warnings`
- `status`

事件增加以下类型：

- `subagent_started`
- `tool_called`
- `tool_completed`
- `evidence_collected`
- `research_followup`
- `conflict_found`
- `subagent_completed`

已有的 `task_failed`、`task_completed` 和 SSE 兼容字段继续保留。

## 失败与降级

- Subagent LLM 不可用：使用当前确定性 Worker 分析逻辑。
- 本地 RAG 不可用：跳过本地证据，尝试允许的外部只读工具。
- 外部搜索未配置、超时或熔断：返回明确 warning，不生成虚构实时事实。
- 单个 Subagent 失败：Supervisor 保留其他 Worker 结果，最终草稿标记该领域不可用。
- Deep Research 达到轮数或调用上限：输出已有证据和未解决问题。
- Evidence 冲突未解决：保留冲突并阻止依赖该事实的确定性结论。

## 测试验收

- 每个 Subagent 的工具白名单和结构化输出测试。
- Deep Research 的多轮、去重、冲突、轮数上限和失败降级测试。
- 五个 Subagent 在 Supervisor 中并行执行的测试。
- 无 LLM、无 MCP、无向量库时的回退测试。
- 外部证据不能生成未被支持的价格、班次、库存和营业状态测试。
- SSE 事件顺序和前端兼容性测试。
- 现有表单幂等、治理、RAG、Supervisor 和安全测试保持通过。

## 非目标

- 不接入购票、预订、支付、退款或外部消息发送。
- 不使用开放式 Swarm 或 Group Chat 作为旅行规划主流程。
- 不在第一阶段实现完整的对话式草稿编辑 Agent。
- 不把网页搜索结果直接当作权威实时数据。

## 验收标准

一次完整旅行规划能够展示：

1. Supervisor 创建并并行调度五个领域 Subagent。
2. 每个 Subagent 使用受限工具并返回带 Evidence 的 `WorkerResult`。
3. 至少一个领域能够触发 Deep Research 的补充查询或冲突处理。
4. Supervisor 根据结果完成路线、预算和草稿生成。
5. 任意外部依赖失败时，系统仍能返回可解释的部分结果和 warning。
6. 日志和 SSE 能还原 Supervisor、Subagent、工具和证据之间的调用关系。
