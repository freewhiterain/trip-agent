# Phase 3：RAG 检索质量增强 设计

## 背景

Phase 2 交付了成都本地模拟 RAG（`LocalKnowledgeService`：BM25 + 空 Dense
+ 词频重叠重排）和轻量 GraphRAG（1 跳关系查询）。参照 RAGFlow 的 RAG
设计做过一轮差距分析（见 `data/documents` 使用现状与
`D:\Desktop\pythoncode\cc\学习\RAGflow\02-RAG设计架构总览.md`）后，确认
了几处低成本高收益的改进点，以及三项当前语料规模下暂不值得投入完整算法、
但值得先把接口和调用方固定下来的重量级能力。

## 目标

在不改变 Worker 层调用方式、不改变 `Evidence` 输出结构的前提下，把
`app/rag` 检索链路的召回和排序质量补齐；同时为未来语料规模扩大后需要的
三项重量级能力（图社区检测、RAPTOR 摘要树、逐句溯源）预留可替换的模块
边界。

## 范围

### A. 检索质量增强（本阶段实现真实算法）

1. 查询构造加权（字段加权 + 同义词词典 + 相邻词组加分）
2. Markdown 感知切分（按标题切分、孤立标题合并、chunk 携带 `section_title`）
3. Dense 检索接入（本地 Ollama `qwen3-embedding:4b`，离线脚本构建 + 单一
   持久化 Chroma collection）
4. CrossEncoder 重排开关（默认关闭，不下载模型；配套 md 文档说明启用方式）

### B. 重量级能力占位（简化实现 + 可替换接口）

5. `GraphCommunityService`：图社区检测简化版
6. `RaptorIndexer`：摘要树简化版
7. `CitationAnnotator`：逐句溯源简化版

B 类三项的真实算法升级路径已单独记录在
`docs/superpowers/specs/2026-07-24-rag-heavyweight-capabilities-future-design.md`，
本阶段只交付简化实现，不在本文档重复算法细节。

### 不做的事

- B 类三项的真实算法（Leiden 社区检测、PageRank、UMAP+GMM 递归聚类、
  逐句相似度阈值放宽）——升级时机和设计见上述未来设计文档。
- 多向量库/按类别分库——统一用一个持久化 Chroma collection，靠
  `city`+`category` metadata 过滤缩小范围。
- 修改 Worker 对 `LocalKnowledgeService`/`Evidence` 的调用方式或字段结构。

## 组件设计

### 1. 查询构造加权

**问题**：现在 `search_destination` 直接把 `f"{destination} {category}
{query}"` 拼接后扔给 BM25，标题命中和正文命中权重完全一样，也没有同义词
容错。

**设计**：

- **字段加权**：依赖组件 2 产出的 `section_title` metadata。构建 BM25
  分词序列时，把 `section_title` 的分词结果按固定倍数（初始设为 3 倍，
  实施时可调）追加进该 chunk 的 token 序列，让标题词在 BM25 的词频统计
  里天然获得更高权重，不改 BM25 算法本身。
- **同义词词典**：新增 `app/rag/synonyms.py`，手写旅行领域同义词组
  （如 `宾馆/酒店/住宿`、`景点/景区/游览地`、`美食/小吃/餐馆`）。查询
  构造时，对 query 分词结果中命中词典的词，把同义词加入检索 token 集合
  （仅用于扩大 BM25 召回候选，不参与字段加权判断）。
- **相邻词组加分**：对 query 做 bigram 切分（`jieba` 分词后取相邻两词
  拼接），若 chunk 原文按原样包含该二元组，在最终打分上额外加一个固定
  加分项。
- **落点**：这三项都在检索发起前的"查询/文档 token 构造"阶段，改动集中在
  `app/rag/retriever.py`（`_init_bm25`、`retrieve`）和新文件
  `app/rag/synonyms.py`，不改变 `HybridRetriever` 的公开接口签名。

### 2. Markdown 感知切分

**问题**：`ParentDocumentSplitter` 用 `RecursiveCharacterTextSplitter`
按字符数硬切，不理解 Markdown 标题结构，可能把标题和正文拆到不同 chunk，
也没有给 chunk 记录"属于哪个小标题"。

**设计**：

- 切分前先按 `### 标题` 正则识别分段边界，每个标题到下一个标题之间的内容
  作为一个候选块；块内容仍受字符数上限约束（超出上限时退回按字符数二次
  切分，保持现有的 parent/child 两级粒度不变）。
- 如果识别出的候选块只有标题、没有正文（比如标题后紧跟下一个标题），
  自动合并进下一个候选块，不允许出现"只有标题没内容"的 chunk。
- 每个 chunk（parent 和 child 两级）的 metadata 新增 `section_title`
  字段，取值为该 chunk 所属的最近标题文本；供组件 1 的字段加权读取。
- 顺带识别代码块（\`\`\`）和表格（`|` 分隔）作为"保护区间"，切分点不落在
  保护区间内部——当前数据里两者都很少出现，这是为未来语料兼容性做的
  低成本处理。
- **落点**：`app/rag/text_splitter.py`（`ParentDocumentSplitter`），公开
  接口 `split_documents(documents) -> (parents, children)` 签名不变，只是
  内部切分策略和产出 metadata 变化。

### 3. Dense 检索接入

**问题**：`HybridRetriever(vectorstore=None, ...)` 导致 Dense 检索这条线
从未真正生效，混合检索退化成纯 BM25。

**设计**：

- 新增 `app/rag/local_embeddings.py`：基于 `langchain_openai.OpenAIEmbeddings`
  构造一个指向本地 Ollama 的 embedding 客户端（`base_url` 指向
  `http://127.0.0.1:11434/v1`，`model="qwen3-embedding:4b"`，`api_key`
  用占位符，因为 Ollama 不校验）。
- 新增离线脚本 `scripts/build_vectorstore.py`（结构参照
  `scripts/build_knowledge_graph.py`）：加载全部文档（不分类别）→ 按
  组件 2 的切分逻辑切成 child chunk → 用本地 Ollama embedding → 写入
  **一个**持久化 Chroma collection（复用现有 `VectorStoreManager` 的
  持久化路径约定，`embeddings` 参数换成本地 Ollama 客户端）。
- `LocalKnowledgeService` 初始化时尝试加载这个持久化 collection；加载
  失败（文件不存在、Ollama 未启动过导致从未构建成功等）时记录日志并把
  `vectorstore` 置为 `None`，检索退化为纯 BM25，不抛异常、不阻塞。
- `search_destination` 不再为每次查询重建 Dense 索引，而是复用这一个共享
  的持久化 vectorstore，查询时传入 `filter={"city": destination,
  "category": category}`（Chroma 原生支持的 metadata 过滤）缩小范围，
  和 BM25 侧"先过滤子集"的效果对齐，但不需要重新 embedding。
- **落点**：`app/agents/workers/local_knowledge.py`（`LocalKnowledgeService.
  __init__`/`search_destination`）、`app/rag/retriever.py`
  （`HybridRetriever.retrieve` 需要接受一个可选的 metadata filter 参数
  透传给 `vectorstore.similarity_search_with_score`）、新增
  `app/rag/local_embeddings.py`、`scripts/build_vectorstore.py`。

### 4. CrossEncoder 重排开关

**问题**：`CrossEncoderReranker` 已存在（`app/rag/reranker.py:49-63`，
懒加载不会导入时下载模型），但从未被 `LocalKnowledgeService` 使用，也没有
配置开关。

**设计**：

- `app/config.py` 新增 `enable_cross_encoder_rerank: bool = False`、
  `cross_encoder_model: str = "BAAI/bge-reranker-base"`（中文友好的开源
  CrossEncoder，体积和精度较均衡；`docs/rag-cross-encoder-setup.md` 里
  说明如需更高精度或更小体积可替换的备选模型）。
- `LocalKnowledgeService._build_retriever` 按 `settings.
  enable_cross_encoder_rerank` 选择 `RelevanceReranker()`（默认）或
  `CrossEncoderReranker(settings.cross_encoder_model)`。
- 新增 `docs/rag-cross-encoder-setup.md`：说明候选模型（中文/多语言
  CrossEncoder）、怎么打开开关、预计下载体积和首次加载延迟、怎么验证
  重排生效（比如对比开关前后同一查询的排序结果）。这份文档只写说明，
  不涉及本阶段自动下载或调用模型。
- **落点**：`app/config.py`、`app/agents/workers/local_knowledge.py`、
  新增 `docs/rag-cross-encoder-setup.md`。开关默认关闭，CI/日常测试不会
  触发模型下载。

### 5. `GraphCommunityService`（简化版）

- 接口：`build_communities(entities, relations) -> list[Community]`。
- 简化实现：每个实体各自成一个社区（`Community(entities=[entity],
  importance=1.0, summary=entity.name)`），不做真实图算法。
- 落点：新增 `app/rag/graph_community.py`，`Community` 数据类定义在
  同文件或 `app/schemas/planning.py`（视实施时字段复杂度决定）。
- 本阶段**不**接入 Worker 或离线构建脚本的主流程——只交付服务类本身和
  单元测试，确认接口可用、行为符合"每实体一社区"的预期。是否接入主流程
  留到真实算法升级、或后续如果发现有具体用途时再评估。

### 6. `RaptorIndexer`（简化版）

- 接口：`build_tree(documents: list[Document]) -> list[Document]`。
- 简化实现：对传入的 child chunk 做一层聚类（可以用简单的按
  `document_id` 分组代替真实聚类算法），每组生成一个摘要 chunk（摘要内容
  可以是"取组内第一个 chunk 的前 N 字"这类不依赖 LLM 的占位摘要，避免
  本阶段引入额外的 LLM 调用），追加进返回列表，不递归。
- 落点：新增 `app/rag/raptor.py`。
- 本阶段同样**不**接入检索主链路，只交付可独立测试的模块。

### 7. `CitationAnnotator`（简化版）

- 接口：`annotate(answer: str, evidence: list[Evidence]) -> AnnotatedAnswer`。
- 简化实现：整段 `answer` 统一标注为传入的全部 `evidence` 来源列表，不做
  逐句拆分或相似度匹配。
- 落点：新增 `app/rag/citation.py`，`AnnotatedAnswer` 作为简单数据类
  （`text: str`, `sources: list[Evidence]`）。
- 本阶段**不**接入生成后处理主链路（当前系统的证据溯源已经通过
  `Evidence`/`is_mock` 在结构化分析阶段实现，逐句标注属于面向未来"更自然
  语言生成"场景的准备），只交付可独立测试的模块。

## 数据流与降级行为

```
查询: destination + category + query
  -> 查询构造加权（同义词扩展 + bigram 识别）
  -> BM25 检索（临时子集索引，沿用现状）
  -> Dense 检索（共享持久化 collection + metadata filter，vectorstore 为
     None 时跳过，不报错）
  -> RRF 融合
  -> 重排（词频重叠 或 CrossEncoder，取决于开关）
  -> parent chunk 回溯
  -> Evidence 列表（结构不变）
```

任一环节缺失（Ollama 未启动、向量库未构建、CrossEncoder 开关关闭）都必须
优雅降级为现有行为，不能让 Worker 主流程失败——这一原则和 Phase 2 本地
知识库、GraphRAG 的既有设计保持一致。

## 测试与验收标准

- 组件 1（查询加权）、2（Markdown 切分）：纯逻辑单元测试，不依赖网络/
  模型。
- 组件 3（Dense）：默认测试用假的/内存 embedding 函数验证 filter 和降级
  行为；真实 Ollama 调用的测试标记为 opt-in（沿用 `RUN_EXTERNAL_TESTS`
  模式），需要用户手动启动 Ollama 才会跑。
- 组件 4（CrossEncoder）：开关关闭路径必须有测试覆盖，确认默认不触发
  `sentence_transformers` 导入；真实模型加载测试同样标记为 opt-in。
- 组件 5-7（B 类）：只需验证简化实现本身的行为符合设计（如"每实体一
  社区""整段统一标注"），不需要集成测试。
- 全量回归：Phase 1/2/GraphRAG 现有测试套件必须保持全绿。
- 验收标准：Worker 层对 `LocalKnowledgeService`/`Evidence` 的调用方式和
  输出结构不变；四项 A 类改动均为检索内部质量提升，不改变对外契约。

## 参考文档

- `D:\Desktop\pythoncode\cc\学习\RAGflow\01-RAG检索核心详解.md`
- `D:\Desktop\pythoncode\cc\学习\RAGflow\02-RAG设计架构总览.md`
- `docs/superpowers/specs/2026-07-24-rag-heavyweight-capabilities-future-design.md`
