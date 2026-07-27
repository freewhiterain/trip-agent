# 用户长期记忆分层架构 设计

## 背景

当前代码里"记忆"相关的能力是**只写不读**：

- `app/memory/service.py`（`MemoryGovernanceService`）+
  `app/governance/postgres.py`（`PostgresPreferenceRepository`）实现了完整、
  经审批的偏好写入链路——用户确认后，偏好真实落库到 Postgres
  `user_preference` 表，还会同步一条到可选的 mem0 语义记忆（未装可选依赖
  时自动降级为 `NullSemanticMemory`，不阻塞主流程）。
- 但 `PostgresPreferenceRepository.list()`（读取某用户全部已确认偏好）
  在整个代码库里没有任何调用方。`Supervisor` 的 `planner_node`、各
  Worker、`synthesize_itinerary_with_llm` 全程只使用当次请求体里的
  `TravelRequirement`，从未读取用户历史上确认过的长期偏好。
- 前端 `1_zhixing.html` 也没有调用 `/preferences/proposals` 的入口——这条
  链路目前完全靠直接调 API 触发。
- 另外存在一套完全孤立的遗留代码：`app/core/store.py` 的
  `UserMemoryService` 类 + `app/core/memory_models.py`
  （`UserProfile`/`TravelHistory`/`TravelRecord`）。写入口已被硬编码禁用
  （`_require_approval` 直接抛 `PermissionError`，提示改用
  `MemoryGovernanceService`），读取也无人调用，是被
  `app/memory/` 取代后未清理的死代码。`app/core/store.py`
  里的连接池/生命周期部分（`StoreManager`/`get_store`/`store_lifespan`）
  仍是活的基础设施（`app/main.py` 启动、`/health/detail` 健康检查都在用），
  与 `UserMemoryService` 类需要分开处理。

为了系统性地补齐这个缺口，本轮参考了开源项目 Mem0
（`D:\Desktop\pythoncode\cc\Mem0`）的记忆架构做了一轮启发调研，识别出哪些
机制值得借鉴、哪些是"技术上优雅但这个领域用不上"，最终确定了下面的分层
设计。

## 目标

1. 把"已确认的用户长期记忆"真正接入规划流程，让它在不覆盖当次显式输入
   的前提下，作为默认值发挥作用。
2. 把当前隐式、零散的记忆概念显式化为一个有文档、有边界的分层架构，而
   不是"偏好表 + 一堆从未被读过的字段"。
3. 用户长期记忆的写入采用 ADD-only（只增不覆盖）语义，天然获得可解释性
   和审计能力。
4. 清理被取代后遗留的死代码。

## 记忆分层架构

### Layer 1 — 会话记忆（短期，现状已经正确，不改动）

载体是 LangGraph Checkpointer，按会话/任务 ID 存当次对话和规划状态，
生命周期是会话/任务级别。这层不需要重新设计，仅在此文档中明确其定位，
与 Layer 2 划清边界。

### Layer 2 — 结构化长期记忆（用户级，本阶段实现）

分两块，共享同一套写入/读取原则：

**2a 偏好画像**：饮食限制、预算区间、住宿风格、出行节奏、行动能力限制
等固定分类的稳定属性。

**2b 行程历史**（本阶段新增概念）：用户确认保存过的正式行程——目的地、
日期、行程中包含的景点。这块不需要 LLM 抽取：写入时机是用户对
`itinerary.save` 审批通过的那一刻，记录内容直接来自已保存的行程内容。
诚实起见，这层记录的语义是"用户确认过的行程计划"，不是"用户实际完成的
旅行"——系统目前没有"旅行结束后回访确认"这个环节，不能虚构"已完成"这个
事实，这与项目现有的"不编造未经证实的事实"原则一致。

共享原则：

- **写入只增不覆盖（ADD-only）**：每次确认写入都是一条新记录（`INSERT`），
  不物理覆盖同 key 的旧记录。读取时取同 key 里确认时间最新的一条作为
  当前值，旧记录保留、可查询，天然获得审计和可解释性（"系统为什么认为我
  喜欢经济型住宿"可以直接追溯到某一次具体的确认）。这是本次设计相对现有
  `PostgresPreferenceRepository.upsert`（按 key 物理覆盖）的关键调整。
- **写入必须经过确认**：无论这条记忆是用户直接陈述的，还是未来系统主动
  从对话中建议的候选，落成"已确认"状态前都必须经过现有的审批流程
  （`ApprovalService`）。这条原则不因为记忆规模或来源而改变。
- **读取是每次创建规划任务的必经步骤**：拉取该用户已确认的偏好画像，
  作为 `TravelRequirement` 对应字段的默认值；**当次请求里已经显式填写
  的字段永远优先，记忆只填补空字段**，绝不覆盖当次输入。行程历史本阶段
  只读取展示、不参与默认值填充（目的地和日期每次都是全新决策，不应该有
  "默认值"）。
- **偏好 key 收敛到固定小词表**，与 `TravelRequirement` 现有字段一一
  对应（列表型：出行风格、饮食偏好、住宿偏好、交通偏好、特殊需求；
  标量型：预算）。词表外的 key 在读取阶段直接忽略，不影响现有写入 API
  的自由度。
- **实体关联复用现有 RAG 知识图谱**：如果未来需要把行程历史里的景点
  关联到"这个景点还出现在哪些资料/其他记忆里"，直接复用 GraphRAG 那套
  已有的实体身份（`KnowledgeEntity`），不为用户记忆单独建一套图引擎——
  域内实体种类有限（目的地、景点），不足以支撑独立图数据库的复杂度。

### Layer 3 — 自由文本/语义记忆（用户级，本阶段仅记录方向，不实现）

装不进 Layer 2 固定分类的内容——一句题外话、说不清类别的偏好表达、
未来可能的图片输入（如用户上传的酒店照片）。特点和边界：

- 检索方式只需要语义（向量）检索本身，不需要 Mem0 那种"语义+关键词+
  实体+时间"四路信号融合——用户级记忆总量注定不大（数十条量级），四路
  融合是为应对海量记忆才有意义的复杂度，这里用不上。
- 产出只作为最终 LLM 生成/对话环节的**叙事性上下文补充**，不用于覆盖或
  填充 Layer 2 的结构化字段——它的确定性不足以驱动结构化决策。
- 写入同样要经过确认；如果未来做"LLM 从对话中主动建议候选记忆"，抽取
  提示词应该收窄到旅行相关的事实类型，而不是通用型抽取。
- 本阶段不实现，仅作为已知的未来扩展点记录在此，避免以后重新调研。

## 不做的事（本阶段范围排除）

- Layer 3 的实现（向量存储、语义检索、LLM 自动抽取候选记忆）。
- 独立的用户记忆图引擎——需要时复用 RAG 侧已有的 `KnowledgeEntity`。
- 程序性记忆（Mem0 的 procedural memory）——本系统的"任务执行"是确定性
  DAG（Planner → Worker → Synthesize），不是 agent 现场摸索出的可复用
  操作流程，没有对应的使用场景。
- agent 级别的记忆范围——只有一个用户可见的助手，不需要按 agent 隔离
  记忆。
- 多模态记忆——当前系统没有任何图片输入场景。
- 前端 UI 触发入口（"记住这个偏好"按钮等）——这是独立的前端工作，本阶段
  只做后端读回和存储语义调整，通过直接调用 API 验证。
- "旅行实际完成"的回访确认流程——Layer 2b 记录的是"确认过的行程计划"，
  不是"已完成的旅行"，两者不是同一件事，后者需要新的产品交互（旅行结束
  后询问用户），不在本阶段范围。

## 组件设计

### 1. 固定偏好词表与校验

新增模块（暂定 `app/memory/`），定义偏好 key 只接受与
`TravelRequirement` 字段对应的固定集合，并按字段类型（列表 / 标量）
校验读取到的历史值；类型不匹配的记录在读取阶段跳过并记录 warning，不
抛异常、不阻塞任务创建。

### 2. 偏好画像的 ADD-only 化

调整 `PostgresPreferenceRepository`（及其背后的 `UserPreference` 表）
的写入语义：从"按 `(user_id, key)` 唯一约束做覆盖式 upsert"改为
"每次确认写入一条新记录"；读取时按 `user_id` + `key` 取确认时间最新的
一条。历史记录不删除、不物理覆盖，具体的表结构调整（是否需要新增版本号
或直接依赖时间戳排序）留给实施计划阶段确定。

需要注意：本项目没有引入 Alembic 之类的迁移工具，schema 变更是靠
`scripts/init_db.py` 运行时的 `Base.metadata.create_all` 生效的，而
`create_all` 只会补建缺失的表/列，不会删除已存在表上的约束。也就是说，
如果某个数据库在这次改动之前已经跑过 `init_db()`，`user_preference`
表上旧的 `uq_user_preference_key` 唯一约束依然会留在库里，需要手动执行
`ALTER TABLE user_preference DROP CONSTRAINT uq_user_preference_key;`
清理掉，否则同一个 key 第二次确认写入时会触发未捕获的 `IntegrityError`
（这条写入路径不像读取路径那样有降级处理）。

### 3. 行程历史记录（Layer 2b，新增）

在 `ItineraryGovernanceService.apply`（用户对 `itinerary.save` 审批
通过、行程正式落库的那一刻）之后，追加写入一条行程历史记录：目的地、
起止日期、行程涉及的景点、来源行程 ID、确认时间。这个追加写入是行程
保存这个主动作的**次要副作用**：如果追加失败，只记录 warning，不影响
行程保存本身是否成功——和现有 Worker 注册表"次要环节失败不拖垮主流程"
的降级原则一致。

### 4. 偏好默认值解析与合并

两个职责清晰分离的函数：一个负责从存储里拉取该用户当前有效的偏好画像
（应用 ADD-only 语义，取每个 key 最新一条，过滤词表外/类型不匹配的
条目）；另一个负责把这份画像和当次请求的 `TravelRequirement` 合并——
只填充请求里为空的字段，已有内容一律不动。合并产出一个新的
`TravelRequirement`，不修改原始输入。

### 5. 接入点

`app/api/v1/planning.py` 的 `create_planning_task`：收到
`TravelRequirement` 后、传给 `run_travel_planning` 前，完成偏好默认值
的拉取与合并。拉取偏好如果因数据库异常失败，按"无长期偏好"降级处理
（视为空画像），不影响本次任务创建。

### 6. 清理死代码

删除 `app/core/memory_models.py` 整个文件，删除 `app/core/store.py`
中的 `UserMemoryService` 类；保留该文件中的 `StoreManager`/`get_store`/
`store_lifespan`（应用启动生命周期和健康检查仍在使用）。

## 数据流与降级行为

```
创建规划任务：
  接收 TravelRequirement
    -> 拉取该用户 Layer 2a 偏好画像（按 key 取最新，词表过滤+类型校验）
       -> 拉取失败：按空画像处理，记录 warning，不阻断任务创建
    -> 只填充请求里为空的字段，已填字段不变
    -> 交给 run_travel_planning（Supervisor 图不变）

保存正式行程（审批通过）：
  ItineraryGovernanceService.apply 保存行程
    -> 追加一条 Layer 2b 行程历史记录
       -> 追加失败：记录 warning，不影响行程保存本身的成功状态
```

任一环节的记忆读取/写入失败都必须优雅降级，不能让规划任务创建或行程
保存这两个主流程失败——与项目现有的降级原则（RAG 检索、Worker 注册表、
外部数据源）保持一致。

## 测试与验收标准

- 固定词表 + 类型校验：合法/非法 key、合法/非法值类型的读取行为。
- ADD-only 写入语义：同一 key 写入两次，两条记录都保留，读取返回确认
  时间最新的一条。
- 偏好合并逻辑：空字段被填充、已填字段不被覆盖、budget 同理、词表外
  字段被忽略。
- 行程历史：`itinerary.save` 审批通过后能读到一条对应的历史记录；追加
  失败不影响行程保存本身的返回结果。
- API 层集成测试：`POST /tasks` 返回的 `draft.requirement` 确认合并了
  历史偏好，且当次显式填写的字段未被覆盖。
- 降级路径测试：偏好读取/行程历史写入的数据库异常不会导致对应主流程
  失败。
- 全量回归：删除死代码后确认无遗留 import，Phase 1/2/3/GraphRAG 现有
  测试套件保持全绿。
- 验收标准：确认过的长期偏好能在不覆盖当次输入的前提下影响新规划任务
  的默认值；确认保存的行程能产生可查询的历史记录；偏好历史不被物理
  覆盖；死代码清理后应用启动和健康检查不受影响。

## 参考

- Mem0 源码与文档：`D:\Desktop\pythoncode\cc\Mem0`
  （`mem0/memory/main.py`、`mem0/configs/prompts.py`、
  `docs/core-concepts/memory-types.mdx`、
  `docs/core-concepts/memory-evaluation.mdx`、
  `docs/platform/features/graph-memory.mdx`、
  `docs/open-source/features/multimodal-support.mdx`、
  `docs/open-source/features/custom-instructions.mdx`）。
