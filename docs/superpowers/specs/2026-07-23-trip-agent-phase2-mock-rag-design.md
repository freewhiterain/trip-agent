# 第二阶段：本地模拟知识库与 RAG Worker 设计

## 目标

在不接入外部实时 API 的前提下，使用项目内的 Markdown 文件作为模拟知识库，验证完整的旅行规划研究流程：

```text
旅行表单
  -> Supervisor
  -> 五类 Worker
  -> RAG 检索本地资料
  -> Worker Agent 分析与结构化输出
  -> Supervisor 汇总
  -> 模拟旅行草稿
```

Markdown 只是知识库原料，不是 Worker 的直接输出。Worker 必须先检索相关证据，再结合旅行目的地、日期和天数进行分析。没有证据时不得编造事实。

## 范围

### 包含

- 增加或整理本地 Markdown 模拟资料。
- 复用现有文档加载、切片、BM25、Dense、RRF 和重排能力。
- 为五类 Worker 增加按职责检索和 Agent 分析流程。
- 统一 Worker 结果、证据和警告格式。
- 让 Supervisor 并行调度五类 Worker 并汇总部分结果。
- 在前端显示 Worker 状态、模拟资料标记、证据来源和缺失资料警告。
- 增加不依赖外部网络的单元测试和端到端测试。

### 不包含

- 天气、地图、航班、铁路、酒店或餐厅的真实 API。
- 实时价格、营业状态、班次和天气结论。
- 新的外部数据源策略。
- 将模拟资料伪装成实时信息。

## 模拟资料结构

按资料职责和目的地分目录：

```text
data/documents/
├── destinations/
├── attractions/
├── weather/
├── transport/
├── accommodation/
└── food/
```

第二阶段先以成都作为完整示例。现有 `destinations/chengdu.md` 继续保留并作为基础资料，其他类别补充对应文件，例如：

```text
data/documents/attractions/chengdu.md
data/documents/weather/chengdu.md
data/documents/transport/chengdu.md
data/documents/accommodation/chengdu.md
data/documents/food/chengdu.md
```

每份资料需要明确声明其测试性质，例如：

```text
数据类型：模拟资料
适用城市：成都
最后更新：开发测试数据
```

资料内容使用稳定、可引用的段落和小标题，便于切片、检索和证据回溯。资料中不写入无法验证的实时承诺。

## RAG 检索层

复用现有的 `DocumentManager`、`ParentDocumentSplitter`、`HybridRetriever`、`RelevanceReranker` 和证据转换逻辑。

检索流程为：

1. 加载本地 Markdown 文档。
2. 为文档添加城市、职责类别、模拟资料类型和来源路径元数据。
3. 切分父文档和子片段。
4. 根据城市、Worker 职责、日期和用户需求构造查询。
5. 通过 BM25 与 Dense 检索获取候选片段。
6. 使用 RRF 合并并进行相关性重排。
7. 返回带来源标识的证据片段。

Worker 查询必须优先限定城市和职责类别，避免景点 Worker 使用住宿资料，或美食 Worker 使用天气资料。若当前 Dense 向量库未配置，保留 BM25 路径作为本地测试降级方案。

## Worker Agent 层

五个 Worker 的职责固定为：

- `attractions`：从景点资料中选择适合当前旅行的景点。
- `weather`：从模拟天气资料中提取日期相关的出行建议，并标记非实时性质。
- `transport`：从交通资料中提取城市内和城市间交通建议。
- `hotel`：从住宿资料中提取区域和住宿类型建议，不生成实时房价或空房结论。
- `food`：从美食资料中选择符合目的地和行程的餐饮建议。

每个 Worker 遵循同一条链路：

```text
ResearchTask + TravelRequirement
  -> 类别限定的 RAG 查询
  -> 证据片段
  -> Worker Agent 提示词
  -> 结构化结果
```

Agent 只能使用检索到的证据。它可以对证据进行筛选、比较、排序和行程适配，但不能补充证据中不存在的价格、时间、班次、营业状态或天气事实。LLM 不可用时，Worker 使用确定性证据摘要作为降级结果，不阻塞整体流程。

Worker 返回统一结构：

```json
{
  "worker": "attractions",
  "status": "completed",
  "is_mock": true,
  "summary": "根据检索到的景点资料筛选适合本次行程的选项。",
  "options": [],
  "evidence": [],
  "warnings": []
}
```

`summary` 是面向系统的简短分析结论，不是内部完整思维过程。`evidence` 保存来源路径和引用片段，供 Supervisor 和前端展示。

## Supervisor 汇总

Supervisor 继续接收已经完整确认的旅行需求，不负责弹出表单或判断用户意图。

执行流程：

1. 创建五类研究任务。
2. 按现有依赖图并行执行相互独立的 Worker。
3. 持久化每个 Worker 的开始、完成、不可用或失败状态。
4. 收集 Worker 结果、证据和警告。
5. 仅使用有证据的选项生成模拟旅行草稿。
6. 在结果中保留每个 Worker 的状态和来源。

单个 Worker 不可用或失败时，其他 Worker 继续执行。没有任何相关证据时，Supervisor 返回明确的资料不足状态，不生成看似真实的具体安排。当前路线编排可以继续使用已有的通用模板，但必须保留“模拟资料”和“需要后续确认”的提示。

## 前端展示

用户提交表单后，前端显示研究流程和五类 Worker 状态：

```text
旅行规划
  ✓ 景点 Worker
  ✓ 天气 Worker
  … 交通 Worker
  ✓ 住宿 Worker
  ✓ 美食 Worker
```

结果页需要：

- 在规划结果顶部显示“当前使用：本地模拟资料”。
- 展示每个 Worker 的状态和简短摘要。
- 支持查看证据来源文件和引用片段。
- 明确显示暂无资料或需要实时确认的项目。
- 不把模拟价格、天气、营业时间或班次渲染为实时事实。

## 错误处理

- Markdown 文件不存在：Worker 返回 `unavailable` 和可读警告。
- 文档没有命中相关证据：Worker 返回资料不足，不调用无依据的补全。
- LLM 未配置或调用失败：使用证据摘要降级，并保留警告。
- 单个 Worker 失败：Supervisor 汇总其余结果并标记部分完成。
- Supervisor 失败：主 Agent 返回明确错误，不伪造行程。
- Tool Result 重复提交：保持现有幂等边界，Supervisor 只执行一次。

## 测试验收

测试不依赖外部网络或真实 API，至少覆盖：

1. 文档加载后能按城市和类别检索到正确片段。
2. Worker 使用检索证据生成结构化结果并保留来源。
3. Worker 不会把其他类别文档作为主要证据。
4. 没有命中资料时返回 `unavailable` 或资料不足状态。
5. LLM 不可用时确定性降级仍能返回结果。
6. Supervisor 并行汇总五类 Worker。
7. 单个 Worker 失败不会丢失其他结果。
8. 前端显示模拟标记、Worker 状态、证据和警告。
9. 完整表单到模拟旅行草稿流程可重复执行且不重复调用 Supervisor。

## 阶段完成标准

用户输入成都、出发日期和旅行天数后，系统能够：

1. 通过五类 Worker 分别检索本地 Markdown。
2. 由 Worker Agent 根据证据进行分析。
3. 由 Supervisor 汇总并生成模拟旅行草稿。
4. 在前端展示完整流程、来源和模拟资料提示。
5. 在缺少资料或 LLM 不可用时稳定降级，不产生虚假事实。
