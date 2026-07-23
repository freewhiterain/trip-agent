# 本地知识图谱（轻量 GraphRAG）设计

## 背景

当前五类 Worker（attractions/weather/transport/hotel/food）都通过
`LocalKnowledgeService.search_destination` 做"按城市+类别限定的文档检索"，
检索结果是扁平的：景点、酒店、交通、美食各自独立检索，互不关联。这足够回答
"这个类别有什么"，但回答不了"这个景点附近有什么住宿""这两个点怎么连接"
这类关系型问题。

用户计划后续持续新增大量本地 Markdown 资料。文档量上来后，景点、片区、
酒店、交通、美食之间天然存在空间/逻辑关系，纯文档检索无法表达这些关系。

参考了开源项目 RAGFlow 的 GraphRAG 实现后，结论是：RAGFlow 那套（Leiden
社区检测、PageRank、多跳遍历、独立图数据库、LLM 驱动的实体/关系抽取）是为
多租户、多后端、海量语料场景设计的，直接照搬对当前单租户、单一 Postgres
后端、语料量仍然很小的项目是过度设计。因此本设计只取"实体关系图作为并行
检索通道"这个核心思路，规模和实现方式按本项目实际情况重新设计。

## 目标

在不引入新基础设施、不破坏现有硬约束（"没有证据不能编造候选"、
"LLM 未配置或调用失败时必须确定性降级，不阻塞流程"）的前提下，给本地知识库
增加一层轻量实体关系图，作为文档检索之外的第二证据通道，供 Worker Agent
分析时一并使用。

## 范围

### 包含

- Postgres 新增 `entities`、`relations` 两张表。
- 文档入库阶段（离线脚本，不在请求路径上）的规则抽取，以及在已配置 LLM 时
  的可选补充抽取。
- 新增 `GraphKnowledgeService.search_related_entities(destination, category,
  query) -> list[Evidence]`，按城市+类别做 1-2 跳查询。
- `attractions`、`hotel` 两个 Worker 先接入图证据（这两类的"附近/同片区"
  关系需求最直接）。
- 为验证链路，给现有五篇成都模拟资料补充少量具名实体（如具体景点名称）和
  片区/临近关系描述。
- 单元测试与端到端测试。

### 不包含

- 社区检测（Leiden）、PageRank、跨文档多跳（>2 跳）遍历。
- 独立图数据库（Neo4j 等）；图数据存在项目已有的 Postgres 里。
- 实体消歧/归一化算法（同名不同指代的合并）。
- 用图检索替代文档检索——图证据是补充通道，不是替代品；`weather`、
  `transport`、`food` 三个 Worker 本阶段不接入图证据，待前两个 Worker 验证
  通过后再评估是否扩展。
- 请求路径上的实时抽取——抽取只发生在离线入库脚本里。

## 数据模型

```
entities
  id                UUID, PK
  city              str，规范化后的城市名（与文档 metadata.city 一致）
  category          str，TaskType 或 "area"（片区类实体）
  name              str，具体实体名称（如"宽窄巷子"，不是"历史街区"这种主题词）
  source_document   str，来源文档路径
  attributes        JSON，别名、简介等附加信息
  created_at        datetime

relations
  id                UUID, PK
  from_entity_id    UUID, FK -> entities.id
  to_entity_id      UUID, FK -> entities.id
  relation_type     str，如 "located_in" / "near" / "connects_to"
  source_document   str，来源文档路径
  confidence        float，规则抽取固定 1.0，LLM 抽取默认 0.6
  created_at        datetime
```

约束：`(city, category, name)` 唯一，防止重复实体；
`(from_entity_id, to_entity_id, relation_type)` 唯一，防止重复关系。
沿用项目现有约定：`Mapped`/`mapped_column`、UUID 主键、JSON 属性列、
索引加在 `city`/`category`/`from_entity_id`/`to_entity_id` 上；不引入
Alembic，继续通过 `Base.metadata.create_all`（与现有表一致）。

## 抽取流程（离线，不在请求路径上）

新增 `scripts/build_knowledge_graph.py`，独立运行，不在 FastAPI 请求路径
上、也不在 `Worker.run()` 里触发：

1. **规则抽取**：扫描文档二级标题和正文，若标题是具体地名（而非"景点主题"
   这类泛主题词）则登记为实体；正文中出现"位于 / 临近 / 属于 XX 片区"等
   固定句式时，用正则规则登记 `located_in` / `near` 关系。规则抽取不依赖
   LLM，任何时候都会执行。
2. **可选 LLM 抽取**：仅当 `settings.dashscope_api_key` 已配置时，对每篇
   文档追加一次结构化抽取调用，复用 `rag_analysis.py` 里"只能使用给定文本、
   不得编造"的约束提示，补充规则抽取覆盖不到的关系。单篇文档抽取失败或未
   配置 LLM 时直接跳过该篇的 LLM 补充，规则抽取结果照常入库，脚本不中断。
3. 抽取结果写入 `entities` / `relations` 表；脚本可重复运行（按唯一约束做
   upsert，不产生重复数据）。

## 检索集成

- `GraphKnowledgeService.search_related_entities(destination, category,
  query)`：按城市+类别过滤实体，查询命中实体的直接关系（1 跳）以及关系另一
  端实体的直接关系（2 跳，仅当另一端是 `area` 类实体时展开，避免跳数爆炸），
  把命中的实体和关系拼成简短文本，转成 `Evidence`
  （`metadata.source_type="graph_relation"`，与 `mock_markdown` 区分）。
- `AttractionsWorker`、`HotelWorker` 在现有 `search_destination` 调用之后，
  追加调用 `search_related_entities`，两组 `Evidence` 合并后一起交给
  `analyze_worker_evidence`；该函数现有的"无证据不产出候选"约束不变，图
  证据只是让候选证据集合更丰富。
- 图谱为空、未建表或查询异常：`GraphKnowledgeService` 捕获异常记录日志，
  返回空列表，等价于"没有图证据"，不影响文档证据路径，Worker 状态判定
  （completed/partial/unavailable）逻辑不变。

## 错误处理

- 数据库不可用/表未建：`search_related_entities` 捕获异常返回空列表，不
  抛出到 Worker，不影响文档检索结果。
- 离线抽取阶段 LLM 调用失败：跳过该文档的 LLM 补充抽取，规则抽取结果仍然
  入库，脚本继续处理下一篇文档，不中断整体抽取。
- 图证据与文档证据在内容上不一致：本阶段不做冲突检测，图证据仅作为补充
  线索随文档证据一起交给 Worker Agent，最终结论仍按现有"仅使用给定证据、
  不得编造"的约束生成，不单独形成事实断言。

## 测试验收

1. 规则抽取单元测试：给定包含具体地名和"位于/临近"句式的文档，验证抽取出
   的实体和关系符合预期，不误把主题词当实体。
2. LLM 抽取未配置/失败：验证抽取脚本仍能完整跑完规则抽取部分，不因 LLM
   失败中断或产生半写入的脏数据。
3. `search_related_entities` 类别隔离测试：验证跨城市、跨类别不会串数据，
   空结果时返回空列表而不是异常。
4. Worker 集成测试：`AttractionsWorker`/`HotelWorker` 在有图证据时，返回
   结果里能体现图证据来源；图为空时行为与现状完全一致（不引入新的失败
   模式，不改变现有 Phase 2 测试的通过结果）。
5. 端到端测试：给成都资料补充的具名实体和至少一条片区/临近关系，跑通一次
   "离线抽取 -> 入库 -> Worker 检索 -> 证据合并"的完整链路。

## 阶段完成标准

- `entities`/`relations` 表建成，`scripts/build_knowledge_graph.py` 可离线
  重复运行，未配置 LLM 时只产出规则抽取结果，不阻塞、不报错退出。
- `attractions`、`hotel` 两个 Worker 在有图证据时能把图证据并入分析输入；
  图为空或异常时行为与当前 Phase 2 完全一致。
- 新增测试全部通过，且不破坏 Phase 1、Phase 2 现有测试（`tests/test_phase1_*`、
  `tests/test_phase2_*` 全量保持通过）。

## 后续阶段（明确不在本设计范围内）

- 扩展图证据到 `weather`/`transport`/`food` Worker。
- 实体消歧、别名合并。
- 若语料规模继续增长到规则抽取/2 跳查询不够用的程度，再评估是否需要更复杂
  的图算法或专用图存储。
