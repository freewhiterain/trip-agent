# Deep Agents 与 Supervisor + Agents-as-tools 多 Agent 架构说明

## 1. 这两个概念分别是什么

### 1.1 Deep Agents

Deep Agents 是一种面向复杂、长任务的 Agent 高层运行框架或 Harness。它不是单独的“某一个 Agent”，也不是一种固定的业务流程，而是为 Agent 提供一组可复用的运行能力，例如：

- 主 Agent 的任务规划与分解；
- 子 Agent 调用；
- Skills（按需加载的专业提示词和知识）；
- 文件或工件管理；
- 上下文压缩与上下文隔离；
- 长任务状态持久化；
- 工具调用、重试、人工介入和中断恢复。

可以把 Deep Agents 理解成：

> 为复杂 Agent 提供规划、记忆、上下文管理和子任务执行能力的高级运行层。

Deep Agents 适合处理需要多轮推理、多个子任务、较长上下文和中间产物的工作，例如深度研究、代码修改、长文档分析和复杂旅行规划。

### 1.2 Supervisor + Agents-as-tools

Supervisor + Agents-as-tools 是一种多 Agent 编排模式：

```text
用户
  ↓
Supervisor（主 Agent）
  ├─ 调用目的地研究 Agent
  ├─ 调用交通 Agent
  ├─ 调用住宿 Agent
  ├─ 调用餐饮 Agent
  └─ 调用验证或规划 Agent
```

Supervisor 负责：

- 维护用户对话和业务状态；
- 理解当前任务；
- 决定是否调用子 Agent；
- 为子 Agent 准备精确的上下文；
- 接收子 Agent 的结构化结果；
- 继续编排、综合和回复用户。

Agents-as-tools 的含义是：子 Agent 被包装成一个工具，Supervisor 通过工具调用的方式使用它。子 Agent 通常不直接和用户交互，而是返回结果给 Supervisor。

## 2. 两者的关系

两者不是互相排斥的技术，也不是同一层面的概念。

| 对比项 | Deep Agents | Supervisor + Agents-as-tools |
|---|---|---|
| 本质 | Agent 运行框架/Harness | 多 Agent 编排模式 |
| 关注点 | 规划、上下文、记忆、Skills、子任务执行 | 谁负责决策、谁调用谁、结果如何返回 |
| 是否规定业务流程 | 不规定 | 规定一个中心 Supervisor 和多个子 Agent |
| 是否必须多 Agent | 不一定 | 通常是 |
| 子 Agent 调用方式 | 可以使用多种方式 | 通常包装成工具调用 |
| 状态管理 | 通常由框架提供或集成 | 需要应用显式设计共享状态和持久化 |
| 适用场景 | 长任务和复杂 Agent 应用 | 需要中心控制和统一用户体验的多 Agent 系统 |

可以这样理解：

> Supervisor + Agents-as-tools 是业务编排结构；Deep Agents 是可以承载这种结构的高级运行环境。

例如，旅行项目可以使用 Deep Agents 提供上下文管理和子 Agent 调用，同时在业务层采用 Supervisor + Agents-as-tools 作为主要编排方式。

## 3. Supervisor + Agents-as-tools 的标准执行过程

一个完整的调用过程通常如下：

```text
1. 用户发送自然语言请求
2. Supervisor 读取当前会话和旅行工作区
3. Supervisor 判断是否需要工具或子 Agent
4. Supervisor 生成结构化的子任务参数
5. 子 Agent 执行检索、计算或专业分析
6. 子 Agent 返回结构化结果和证据
7. Supervisor 根据结果继续调用其他工具，或生成回复
8. 验证器检查结果
9. Supervisor 向用户解释结果、询问确认或输出草稿
```

重要的是：Supervisor 不应该只负责把多个文本拼在一起。它还要负责：

- 维护跨轮次状态；
- 处理子任务之间的依赖；
- 判断结果是否充分；
- 发现冲突并要求重新检索或重新规划；
- 在高风险写操作前请求批准。

## 4. Deep Agents 的典型组成

Deep Agents 通常由以下部分组成：

### 4.1 主 Agent

主 Agent 维护主要对话上下文，负责理解任务、拆分任务和综合结果。

### 4.2 Subagents

Subagents 用于隔离复杂子任务的上下文。主 Agent 可以把一个独立问题交给子 Agent，子 Agent 完成任务后只返回摘要、证据和结构化结果。

子 Agent 的价值不只是“多一个模型”，还包括：

- 防止主对话上下文膨胀；
- 让不同领域使用不同提示词和工具；
- 允许独立测试和扩展；
- 支持并行执行相互独立的任务。

### 4.3 Skills

Skills 是按需加载的专业能力，通常包括专业提示词、规则、知识和脚本。它比完整 Subagent 更轻量，适用于“同一个 Agent 只是在不同任务中加载不同专业知识”的场景。

### 4.4 Planning 与上下文管理

长任务需要保存：

- 当前目标；
- 已完成的子任务；
- 中间结果；
- 待处理任务；
- 用户已经确认的约束；
- 当前版本的输出。

不能只依赖一段不断增长的聊天记录。

## 5. 旅行规划项目中的正确落地方式

旅行规划不是“几个 Agent 各自推荐一些内容，然后拼成答案”，而是一个受时间、空间、预算、营业时间和用户偏好共同约束的规划问题。

### 5.1 用户自然语言层

用户可以随意提问：

```text
哈尔滨有什么适合带孩子去的地方？
```

```text
我从上海出发，想去哈尔滨玩 7 天，预算 6000 元。
```

```text
第二天不要安排室外活动，行程轻松一点。
```

不应该在聊天入口强制要求所有消息都包含出发地、日期和天数。

### 5.2 Trip Workspace

系统应该维护一个结构化的旅行工作区，而不是每轮只重新解析一条消息：

```json
{
  "trip_brief": {
    "origin": "上海",
    "destination": "哈尔滨",
    "departure_date": "2026-07-19",
    "days": 7,
    "travelers": {"adults": 2, "children": 1}
  },
  "constraints": {
    "hard": [],
    "soft": [],
    "priorities": []
  },
  "candidate_pool": [],
  "evidence": [],
  "current_itinerary": null,
  "itinerary_versions": [],
  "unresolved_conflicts": []
}
```

用户后续的每句话都应该被理解为对这个工作区的：

- 新增约束；
- 修改约束；
- 查询问题；
- 研究请求；
- 行程变更请求；
- 确认或撤销操作。

这比给每句话分配一个单一 intent 更适合真实旅行对话，因为一条消息可能同时包含多个变更。

### 5.3 研究阶段：可以并行

当目的地、日期范围和基本人数已经明确后，可以启动多个相互独立的研究 Agent：

- Destination Research Agent：检索景点、活动和区域信息；
- Intercity Transport Agent：查询出发地到目的地的交通方式；
- Accommodation Agent：检索住宿区域和候选住宿；
- Food Agent：检索餐饮候选；
- Weather Agent：查询天气、季节和室内外活动条件。

这些 Agent 的输出必须是结构化候选项和 Evidence，而不是只有一段自然语言：

```json
{
  "candidate_id": "poi_001",
  "name": "哈尔滨冰雪大世界",
  "category": "attraction",
  "location": {"lat": 45.75, "lng": 126.58},
  "opening_hours": [],
  "estimated_duration_minutes": 240,
  "source": "...",
  "freshness": "...",
  "confidence": 0.86
}
```

### 5.4 规划阶段：不能与研究阶段完全并行

路线规划必须等待候选数据和交通数据返回。它需要处理：

- 景点之间的距离和交通时间；
- 每个景点的开放时间；
- 建议游览时长；
- 每天可用时间；
- 酒店位置；
- 用餐时间和位置；
- 天气和室内外条件；
- 预算和用户偏好。

因此更合理的依赖关系是：

```text
旅行需求
  ↓
并行研究 Agent
  ↓
统一候选数据池
  ↓
路线规划 Agent / 排程算法
  ↓
行程编排 Agent
  ↓
约束验证器
  ↓
行程草稿
```

路线规划 Agent 可以使用 LLM 理解用户偏好，但时间、距离、开放时间和预算等硬约束应该由代码、地图接口或优化算法验证，不能完全依赖模型生成。

### 5.5 验证与修复阶段

生成草稿后必须检查：

- 是否存在景点重复；
- 是否在营业时间内；
- 景点之间是否来得及移动；
- 是否跨城折返；
- 是否超出每天可用时间；
- 是否超出预算；
- 住宿和餐厅是否与路线匹配；
- 证据是否过期或互相冲突。

验证失败后，不应该直接输出错误行程，而应该：

1. 自动替换冲突候选；
2. 重新排程相关日期；
3. 如果无法同时满足约束，向用户解释取舍并请求选择。

## 6. 当前项目与目标架构的差距

当前项目已经有一个初步的 Planner-Worker 结构，但还不完整：

### 已有部分

- Supervisor；
- 目的地、交通、住宿、餐饮、天气 Worker；
- Evidence 数据结构；
- LangGraph Checkpointer；
- 任务事件和 SSE；
- 旅行需求结构化模型。

### 当前主要问题

1. 所有 Worker 研究任务被同时发送，没有真正使用任务依赖；
2. 没有独立的路线规划和时间排程模块；
3. 合成器只取第一个目的地和餐饮结果；
4. 交通、住宿、天气结果没有进入每日行程排程；
5. `_build_draft()` 使用固定模板循环生成每天内容；
6. 没有统一的 Trip Workspace；
7. 没有验证失败后的局部修复循环；
8. 聊天入口把所有问题都强制当成完整行程创建请求。

因此，当前输出重复的根本原因不是 Agent 数量少，而是：

> 研究结果没有进入真正的路线规划和约束验证阶段，最后仍然由硬编码模板生成行程。

## 7. Deep Agents 和 Supervisor 在本项目中的推荐组合

推荐使用以下组合：

```text
Travel Concierge（主对话 Agent）
  ├─ 直接回答普通旅行问题
  ├─ 调用 RAG 检索工具
  ├─ 调用实时信息工具
  ├─ 启动旅行研究子任务
  └─ 读取并修改 Trip Workspace

create_itinerary（复杂规划工具）
  └─ Supervisor 工作流
      ├─ 研究任务并行执行
      ├─ 候选数据归一化
      ├─ 路线规划和时间排程
      ├─ 行程编排
      ├─ 约束验证
      └─ 冲突修复
```

Deep Agents 负责提供长任务、子 Agent、上下文隔离、Skills 和中间结果管理能力；Supervisor + Agents-as-tools 负责旅行项目内部的业务编排。

## 8. 选择原则

### 适合使用 Deep Agents 的情况

- 研究任务较长；
- 需要多个子任务和中间产物；
- 需要上下文隔离；
- 需要人工中断和恢复；
- 需要跨轮次继续复杂任务。

### 适合使用 Supervisor + Agents-as-tools 的情况

- 需要一个统一的用户交互入口；
- 子 Agent 负责不同专业领域；
- 需要主 Agent 统一综合结果；
- 需要按需并行调用多个研究 Agent。

### 不应该做的事情

- 不要让每个 Worker 都直接面向用户；
- 不要让 Worker 只返回自然语言长文本；
- 不要让 LLM 单独决定路线时间和可行性；
- 不要在聊天入口强制所有问题填写完整旅行字段；
- 不要把 RAG、天气、交通误认为互相排斥的业务意图；
- 不要研究完成后直接使用固定模板拼接每天行程。

## 9. 参考资料

- [LangChain Multi-agent patterns](https://docs.langchain.com/oss/python/langchain/multi-agent)
- [LangChain Subagents](https://docs.langchain.com/oss/python/langchain/multi-agent/subagents)
- [LangChain Router](https://docs.langchain.com/oss/python/langchain/multi-agent/router)
- [OpenAI：A practical guide to building agents](https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/)
- [Anthropic：Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Google Research：Optimizing LLM-based trip planning](https://research.google/blog/optimizing-llm-based-trip-planning/)
- [TravelPlanner benchmark](https://arxiv.org/abs/2402.01622)
- [ChinaTravel benchmark](https://arxiv.org/abs/2412.13682)
- [Flex-TravelPlanner benchmark](https://arxiv.org/abs/2506.04649)
- [Constraint-Aware Multi-Agent Optimization Framework](https://journal.hep.com.cn/fcs/EN/10.1007/s11704-026-52005-y)
