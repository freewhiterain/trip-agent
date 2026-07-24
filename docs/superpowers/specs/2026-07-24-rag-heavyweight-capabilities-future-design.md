# RAG 重量级能力：未来升级设计（GraphCommunity / RAPTOR / 逐句溯源）

> 这不是一份可以直接排期实现的 spec，而是一份"以后语料变大了，该怎么把
> Phase 3 里的简化实现换成真实算法"的设计笔记。Phase 3 只会交付每个模块
> 的最简版本（见 Phase 3 spec），这份文档描述的是简化实现之后的路。

## 为什么现在不做真实算法

三个模块（图社区检测、RAPTOR 摘要树、逐句溯源）在 RAGFlow 里都是为
"语料量大、关系复杂"的场景设计的重活。现在只有成都几份手写 Markdown，
真实算法跑出来的结果和简化版没有实质区别，却要多背图算法库、聚类库、
反复调用 LLM 的成本。所以 Phase 3 只搭架子、把接口和调用方固定下来，
真实算法留到语料规模变化之后再填——这样升级时只替换模块内部实现，不
改调用方代码。

## 什么时候该升级

不是拍脑袋定时间，而是看信号：

- **GraphCommunityService**：`knowledge_entity`/`knowledge_relation` 表的
  关系数量明显超过"手工能看懂全部关系"的规模（经验上几百条关系以上），
  且同一批实体之间出现了肉眼能看出的"聚类"现象（比如某几个景点、住宿
  片区反复互相提及）时，简化版"每个实体自成一个社区"就失去意义了，
  该换真实社区检测。
- **RaptorIndexer**：当单个目的地/类别下的文档数量多到"一层聚类摘要"
  已经不能把语义压缩到可读范围（比如成都一个类别下的文档从 1 份变成
  几十份）时,该加递归层级。
- **CitationAnnotator**：当 Worker 分析结果不再是"一个类别对应一份
  证据文档"，而是一次分析要综合多个证据来源、用户开始追问"这句话是
  哪来的"时,该做逐句匹配。

## 1. GraphCommunityService → 真实社区检测 + PageRank

**现有接口**（Phase 3 简化版）：
```
GraphCommunityService.build_communities(entities, relations) -> list[Community]
# 简化实现：每个实体各自成一个社区，重要度全部相等
```

**升级设计**：

- 算法选型：Leiden（比 Louvain 更稳定，RAGFlow 用的也是这个），Python
  生态里 `python-igraph` + `leidenalg`，或者更轻量的 `networkx` 自带的
  `greedy_modularity_communities`（依赖更少，精度略低，语料量不大时够用，
  可以作为比引入 igraph 更低成本的第一步）。
- 输入：`KnowledgeEntity`/`KnowledgeRelation` 表里的全部数据，构建成一张
  无向图（实体为节点，`relation_type` 为边）。
- 重要度：图算好之后跑 PageRank（`networkx.pagerank`），得到每个实体在
  图里的重要度分数，取代简化版里"全部相等"的默认值。
- 社区摘要：每个社区里的实体名称 + 关系列表拼成一段文字，丢给 LLM 生成
  一段摘要（这一步类似 RAGFlow 的社区摘要生成，是唯一需要 LLM 调用的
  地方，且只在离线构建脚本 `scripts/build_knowledge_graph.py` 里跑，不
  在请求路径上）。
- 输出结构不变，仍然是 `list[Community]`（每个 `Community` 内的实体列表、
  重要度、摘要文本），调用方（Worker 侧的图证据合并逻辑）不需要改。
- 失败兜底：图算法或 LLM 摘要失败时，退回简化版行为（每个实体自成一个
  社区），不能让图谱查询失败连累 Worker 主流程——这条约束和 Phase 3 里
  `search_related_entities` 的"图错误不冒泡"原则一致，升级时必须延续。

## 2. RaptorIndexer → 真实递归聚类

**现有接口**（Phase 3 简化版）：
```
RaptorIndexer.build_tree(documents) -> list[Document]
# 简化实现：对 chunk 做一层聚类 + 摘要，不递归，返回值追加进检索索引
```

**升级设计**：

- 算法选型：和 RAGFlow 一致，embedding 后先用 UMAP 降维，再用
  GMM（高斯混合模型，`sklearn.mixture.GaussianMixture`）或层次聚类
  （AHC）分簇——小数据量时 UMAP 效果不稳定，可以先只用 GMM/AHC 直接在
  原始向量维度上聚类，语料量大到 UMAP 能训练出稳定投影时再加上降维这
  一步。
- 递归终止条件：簇内文档数量少于某个阈值（比如 3-5 篇）或者聚类轮数
  达到上限（比如 3 层）时停止递归——不是聚到只剩一个根节点为止,那样
  会把所有语义压成一句话,反而丢信息。
- 每一层的摘要生成：和社区摘要一样,用 LLM 把一簇 chunk 的内容压缩成一
  段摘要文本，重新 embedding 后作为"更高层级"的 chunk 存进索引，和原始
  chunk 用同一套 Dense 检索逻辑处理，不需要额外的分支判断——这是 RAPTOR
  设计的核心：检索时不区分是原始内容还是摘要。
- 更新策略：这一步比图谱构建更依赖离线批处理，因为每次文档增删都要重新
  聚类+摘要。建议做成和 `scripts/build_vectorstore.py` 同一个离线脚本
  串起来跑，不做增量更新（增量聚类的一致性成本更高，语料量到需要 RAPTOR
  的规模时,离线全量重建的耗时也是可接受的）。

## 3. CitationAnnotator → 真实逐句相似度匹配

**现有接口**（Phase 3 简化版）：
```
CitationAnnotator.annotate(answer, evidence) -> AnnotatedAnswer
# 简化实现：整段 answer 统一标注为 evidence 的来源列表，不做逐句拆分
```

**升级设计**：

- 按句子切分 `answer`（中文可以先按句号/问号/感叹号切，不需要专门的
  分句模型）。
- 对每一句，和 `evidence` 里的每条候选算相似度（可以直接复用
  `RelevanceReranker`/`CrossEncoderReranker` 现成的打分逻辑，不需要再造
  一套——这也是为什么 Phase 3 先把 CrossEncoder 接口理顺是有意义的
  前置工作）。
- 阈值策略照抄 RAGFlow 的思路：先用较高的相似度阈值找匹配来源，找不到
  时逐步放宽阈值，直到至少能标出一个来源；全部放宽后仍找不到的句子，
  保留不标注（不能为了凑引用而乱标）。
- 输出结构：`AnnotatedAnswer` 从"整段一个来源列表"变成"每句一个来源
  列表"，前端展示逻辑需要相应从"整段展示"改成"逐句展示"——这是三个
  模块里唯一需要动到前端的一个，升级时要提前评估前端改动量。

## 升级时的通用注意事项

- 三个模块升级都建议先在测试语料（或者复制一份放大过的假数据）上跑一遍
  离线脚本,人工看几条结果是否合理,再决定要不要接入检索/生成主链路——
  和真实语料规模脱节的算法参数（UMAP 邻居数、Leiden 分辨率、相似度阈值）
  经常需要根据实际数据分布微调,不是一次写死就能用。
- 三个模块都不应该出现在请求路径上（图谱构建、摘要聚类都属于离线批处理，
  逐句溯源例外——它必须在生成后同步执行，所以升级时要重点评估它给回答
  延迟带来的影响，必要时做成可关闭的开关）。
- 每个模块升级仍然要保留"失败不影响主流程"的兜底行为，这是贯穿本项目
  RAG 设计（本地知识库、GraphRAG、Phase 3）的一条不变原则。
