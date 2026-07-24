# RAG Phase 3 检索质量增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `app/rag` 检索链路的查询构造、切分、Dense 检索、重排四项质量短板补齐，并为未来的图社区检测 / RAPTOR / 逐句溯源三项重量级能力预留可替换接口。

**Architecture:** 在不改变 `LocalKnowledgeService`/`Evidence` 对外契约的前提下分层改造 `app/rag`：Markdown 感知切分（Task 1）为查询加权提供标题字段；查询构造加权 + 同义词扩展 + 相邻词组加分（Task 2）提升 BM25 召回质量；Dense 检索（Task 3）用本地 Ollama embedding 写入**一个**持久化 Chroma collection，查询时用 `city`+`category` metadata 过滤缩小范围，失败时自动降级为纯 BM25；CrossEncoder 重排（Task 4）作为默认关闭的开关接入，不触发模型下载。Task 5-7 分别交付图社区检测、RAPTOR 摘要树、逐句溯源三项能力的简化实现，只搭接口和最简算法，不接入主链路，为以后语料变大时的真实算法升级占位（升级设计见 `docs/superpowers/specs/2026-07-24-rag-heavyweight-capabilities-future-design.md`）。

**Tech Stack:** LangChain（`langchain-chroma`、`langchain-openai`、`langchain-text-splitters`）、`rank_bm25`、`jieba`、`sentence-transformers`（均为已有依赖，不新增 pyproject 依赖）、本地 Ollama（`qwen3-embedding:4b`，OpenAI 兼容接口 `http://127.0.0.1:11434/v1`）。

## Global Constraints

- Worker 层对 `LocalKnowledgeService.search_destination`/`search`、`Evidence` 的调用方式和字段结构不变（`AttractionsWorker`/`HotelWorker`/`TransportWorker`/`WeatherWorker`/`FoodWorker` 均不修改）。
- 只用一个持久化 Chroma collection（名称 `local_mock_dense`），不按类别拆分多个向量库；查询时用 `city`+`category` metadata 过滤缩小范围，不重建索引。
- Dense 检索、CrossEncoder 加载在运行时失败都必须优雅降级为已有行为（纯 BM25 / 词频重叠重排），不能抛出异常阻塞 Worker。
- `CrossEncoderReranker` 默认关闭（`enable_cross_encoder_rerank=False`）；关闭状态下任何测试或请求路径都不能触发 `sentence_transformers` 导入或模型下载。
- 本地 Dense embedding 使用已在本机运行的 Ollama（`http://127.0.0.1:11434/v1`，模型 `qwen3-embedding:4b`），通过已有依赖 `langchain-openai` 的 OpenAI 兼容客户端接入，不新增 pyproject 依赖。
- Task 5-7（`GraphCommunityService`/`RaptorIndexer`/`CitationAnnotator`）本阶段只交付简化实现和独立单元测试，不接入 Worker 或任何离线构建脚本的主流程。
- 涉及真实网络/模型调用的测试（真实 Ollama embedding、真实 CrossEncoder 模型下载）必须标记 `pytest.mark.external` 并用专属环境变量 `skipif` 门控（`RUN_OLLAMA_TESTS`、`RUN_CROSS_ENCODER_TESTS`，沿用项目里 `RUN_POSTGRES_TESTS` 的模式），日常测试运行不触发。
- 全量回归：Phase 1 / Phase 2 / GraphRAG 现有测试套件（`python -m pytest -q`）必须保持全绿。

---

### Task 1: Markdown 感知切分

**Files:**
- Modify: `app/rag/text_splitter.py`
- Test: `tests/test_markdown_splitter.py`

**Interfaces:**
- Consumes: 无（只依赖已有的 `document_id`/`stable_hash`，来自 `app/rag/identifiers.py`）
- Produces: `ParentDocumentSplitter.split_documents(documents: list[Document]) -> tuple[list[Document], list[Document]]`（签名不变），parent/child 两级 chunk 的 `metadata["section_title"]`（`str`，无标题时为 `""`）供 Task 2 使用。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_markdown_splitter.py`：

```python
from langchain_core.documents import Document

from app.rag.text_splitter import ParentDocumentSplitter


def test_children_carry_section_title_from_nearest_heading():
    source = Document(
        page_content=(
            "### 成都大熊猫繁育研究基地\n位于成华区。是熊猫文化主题下的代表性自然教育地点。\n\n"
            "### 宽窄巷子\n位于青羊区。是历史街区主题下的代表性步行游览区域。\n"
        ),
        metadata={"source": "chengdu.md"},
    )
    splitter = ParentDocumentSplitter()

    _, children = splitter.split_documents([source])

    titles = {child.metadata["section_title"] for child in children}
    assert titles == {"成都大熊猫繁育研究基地", "宽窄巷子"}
    for child in children:
        if child.metadata["section_title"] == "成都大熊猫繁育研究基地":
            assert "成华区" in child.page_content


def test_lone_heading_merges_into_next_section_instead_of_becoming_empty_chunk():
    source = Document(
        page_content="### 孤立标题\n### 宽窄巷子\n位于青羊区。是历史街区主题下的代表性步行游览区域。\n",
        metadata={"source": "chengdu.md"},
    )
    splitter = ParentDocumentSplitter()

    _, children = splitter.split_documents([source])

    assert len(children) == 1
    assert children[0].metadata["section_title"] == "宽窄巷子"
    assert "孤立标题" in children[0].page_content
    assert "青羊区" in children[0].page_content


def test_no_heading_document_still_splits_by_character_size_as_before():
    source = Document(page_content="成都文化与美食。宽窄巷子。", metadata={"source": "chengdu.md"})
    splitter = ParentDocumentSplitter(
        parent_chunk_size=10, parent_chunk_overlap=2, child_chunk_size=5, child_chunk_overlap=1
    )

    parents, children = splitter.split_documents([source])

    assert len(parents) > 1
    assert all(child.metadata["section_title"] == "" for child in children)


def test_splitter_generates_stable_document_and_chunk_ids():
    source = Document(page_content="成都文化与美食。宽窄巷子。", metadata={"source": "chengdu.md"})
    splitter = ParentDocumentSplitter(
        parent_chunk_size=10, parent_chunk_overlap=2, child_chunk_size=5, child_chunk_overlap=1
    )

    first_parents, first_children = splitter.split_documents([source])
    second_parents, second_children = splitter.split_documents([source])

    assert [item.metadata["parent_id"] for item in first_parents] == [
        item.metadata["parent_id"] for item in second_parents
    ]
    assert [item.metadata["chunk_id"] for item in first_children] == [
        item.metadata["chunk_id"] for item in second_children
    ]
```

（最后一个测试是 `tests/test_phase2_rag.py` 里已有断言的等价重复，用来在改动 `text_splitter.py` 时本地快速验证稳定 ID 行为不回归；`tests/test_phase2_rag.py` 原测试保留不动。）

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_markdown_splitter.py -v`
Expected: `test_children_carry_section_title_from_nearest_heading` 和
`test_lone_heading_merges_into_next_section_instead_of_becoming_empty_chunk`
两个测试 FAIL（`KeyError: 'section_title'`），其余两个 PASS（因为还没改代码，
现有按字符切分行为本来就满足）。

- [ ] **Step 3: 实现 Markdown 感知切分**

替换 `app/rag/text_splitter.py` 全文为：

```python
"""
文本切分：Markdown 标题感知 + 父文档 + 子文档策略
"""
import re
from typing import List, Tuple
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.utils.logger import app_logger
from app.rag.identifiers import document_id, stable_hash


HEADING_PATTERN = re.compile(r"^### (.+)$", re.MULTILINE)


def _segment_by_heading(text: str) -> List[Tuple[str, str]]:
    """按 `### 标题` 切分为 (section_title, section_text) 列表。

    没有匹配到任何三级标题时，整段文本作为一个 section_title="" 的小节。
    """
    matches = list(HEADING_PATTERN.finditer(text))
    if not matches:
        return [("", text)]

    sections: List[Tuple[str, str]] = []
    if matches[0].start() > 0:
        sections.append(("", text[: matches[0].start()]))

    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((title, text[start:end]))

    return sections


def _merge_lone_headings(sections: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """把只有标题、没有正文的孤立小节合并进下一个小节，避免产生空内容 chunk。"""
    merged: List[Tuple[str, str]] = []
    pending_prefix = ""
    for title, body in sections:
        content_without_heading = HEADING_PATTERN.sub("", body, count=1).strip()
        if not content_without_heading:
            pending_prefix += body if body.endswith("\n") else body + "\n"
            continue
        merged.append((title, pending_prefix + body))
        pending_prefix = ""
    if pending_prefix:
        if merged:
            last_title, last_body = merged[-1]
            merged[-1] = (last_title, last_body + pending_prefix)
        else:
            merged.append(("", pending_prefix))
    return merged


class ParentDocumentSplitter:
    """
    父文档切分器

    策略：
    - 先按 Markdown 三级标题（`### 标题`）切成小节，孤立标题自动并入下一节
    - 每个小节内部：父文档 1000 字符/块，子文档 200 字符/块
    - 每个 chunk 携带 metadata["section_title"]，供查询加权使用
    """

    def __init__(
            self,
            parent_chunk_size: int = 1000,
            parent_chunk_overlap: int = 200,
            child_chunk_size: int = 200,
            child_chunk_overlap: int = 50
    ):
        self.parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=parent_chunk_size,
            chunk_overlap=parent_chunk_overlap,
            separators=["\n\n", "\n", "。", "，", " ", ""]
        )
        self.child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=child_chunk_size,
            chunk_overlap=child_chunk_overlap,
            separators=["\n\n", "\n", "。", "，", " ", ""]
        )

    def split_documents(self, documents: List[Document]) -> Tuple[List[Document], List[Document]]:
        """
        切分文档为父文档和子文档

        返回：
        - parent_docs: 父文档列表
        - child_docs: 子文档列表（包含 parent_id、section_title）
        """
        parent_docs = []
        child_docs = []

        for doc in documents:
            doc_id = document_id(doc)
            sections = _merge_lone_headings(_segment_by_heading(doc.page_content))

            parent_index = 0
            for section_title, section_text in sections:
                section_doc = Document(page_content=section_text, metadata=dict(doc.metadata))
                parent_chunks = self.parent_splitter.split_documents([section_doc])

                for parent_chunk in parent_chunks:
                    parent_id = stable_hash(doc_id, "parent", parent_index)
                    parent_index += 1
                    parent_chunk.metadata["document_id"] = doc_id
                    parent_chunk.metadata["parent_id"] = parent_id
                    parent_chunk.metadata["section_title"] = section_title
                    parent_docs.append(parent_chunk)

                    child_chunks = self.child_splitter.split_documents([parent_chunk])
                    for child_index, child_chunk in enumerate(child_chunks):
                        child_chunk.metadata["document_id"] = doc_id
                        child_chunk.metadata["parent_id"] = parent_id
                        child_chunk.metadata["section_title"] = section_title
                        child_chunk.metadata["chunk_id"] = stable_hash(
                            parent_id,
                            "child",
                            child_index,
                            child_chunk.page_content,
                        )
                        child_docs.append(child_chunk)

        app_logger.info(f"切分完成: {len(parent_docs)} 个父文档, {len(child_docs)} 个子文档")
        return parent_docs, child_docs
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_markdown_splitter.py -v`
Expected: 4 个测试全部 PASS。

- [ ] **Step 5: 跑一遍现有 RAG 回归测试确认没有破坏 Phase 1/2**

Run: `python -m pytest tests/test_phase2_rag.py tests/test_phase2_rag_workers.py tests/test_phase2_mock_rag_e2e.py -v`
Expected: 全部 PASS（`ParentDocumentSplitter` 对无标题文档的行为保持不变）。

- [ ] **Step 6: Commit**

```bash
git add app/rag/text_splitter.py tests/test_markdown_splitter.py
git commit -m "feat(rag): add markdown heading-aware chunking with lone-heading merge"
```

---

### Task 2: 查询构造加权（字段加权 + 同义词 + 相邻词组）

**Files:**
- Create: `app/rag/synonyms.py`
- Modify: `app/rag/retriever.py`
- Test: `tests/test_query_weighting.py`

**Interfaces:**
- Consumes: `HybridRetriever.__init__(vectorstore, documents, k, parent_documents, reranker)`（签名不变）；`Document.metadata["section_title"]`（Task 1 产出）。
- Produces: `app.rag.synonyms.expand_synonyms(terms: list[str]) -> list[str]`；`HybridRetriever.retrieve` 行为增强（标题加权 + 同义词召回 + 相邻词组加分），公开签名新增可选关键字参数 `metadata_filter`（本任务先加参数占位，实际使用在 Task 3）。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_query_weighting.py`：

```python
from langchain_core.documents import Document

from app.rag.retriever import HybridRetriever
from app.rag.synonyms import expand_synonyms


def test_expand_synonyms_returns_group_members_without_original_term():
    expanded = expand_synonyms(["酒店"])
    assert set(expanded) == {"宾馆", "住宿"}


def test_expand_synonyms_ignores_terms_outside_the_dictionary():
    assert expand_synonyms(["熊猫"]) == []


def test_title_weighted_document_outranks_document_without_matching_title():
    # untitled 排在列表第一位：如果标题加权是空操作，两个文档在 "熊猫基地" 上
    # 都不命中、同分，Python 的稳定排序会让列表里排在前面的 untitled 胜出，
    # 断言就会失败——只有标题加权真的生效时 titled 才会反超排到第一。
    #
    # decoy 是必需的第三篇文档：rank_bm25 的 IDF 公式在语料只有 2 篇文档、
    # 命中词只出现在其中 1 篇时精确等于 log(1.5/1.5) == 0，会把标题加权的
    # 词频信号直接乘没，导致测试即使在生产代码正确时也会失败。加入一篇完全
    # 不命中查询词的第三篇文档，让该词的文档频率从 1/2 变为 1/3，IDF 才会
    # 变为正数，标题加权的词频差异才能真正体现在分数上。
    shared_body = "位于城市东北部，环境优美，适合家庭游玩。"
    untitled = Document(
        page_content=shared_body,
        metadata={"chunk_id": "untitled", "section_title": "宽窄巷子"},
    )
    titled = Document(
        page_content=shared_body,
        metadata={"chunk_id": "titled", "section_title": "熊猫基地"},
    )
    decoy = Document(
        page_content="夜市小吃很受欢迎，适合傍晚游览。",
        metadata={"chunk_id": "decoy", "section_title": "锦里"},
    )
    retriever = HybridRetriever(None, [untitled, titled, decoy], k=2)

    result = retriever.retrieve("熊猫基地")

    assert result[0].metadata["chunk_id"] == "titled"


def test_synonym_expansion_recalls_document_using_different_wording():
    # unrelated 排在列表第一位，原因同上：如果同义词扩展是空操作，"酒店" 在
    # 两个文档里都不命中、同分，稳定排序会让 unrelated 胜出，断言失败——只有
    # 同义词扩展真的把 "住宿" 拉进检索词，hotel_doc 才会反超排到第一。
    #
    # decoy 同样是必需的第三篇文档，原因与上一测试相同：命中词只出现在 1/2
    # 篇文档时 IDF 精确为 0，同义词扩展带来的召回信号会被直接乘没。加入一篇
    # 不含 "住宿"/"酒店" 的第三篇文档，把文档频率变为 1/3，IDF 才会为正。
    unrelated = Document(page_content="成都天气常年温和湿润。", metadata={"chunk_id": "weather"})
    hotel_doc = Document(page_content="青羊区住宿片区靠近宽窄巷子。", metadata={"chunk_id": "hotel"})
    decoy = Document(page_content="熊猫基地全年开放，游客络绎不绝。", metadata={"chunk_id": "decoy"})
    retriever = HybridRetriever(None, [unrelated, hotel_doc, decoy], k=2)

    result = retriever.retrieve("酒店")

    assert result[0].metadata["chunk_id"] == "hotel"


def test_bigram_match_boosts_exact_phrase_over_scattered_terms():
    # scattered 特意让 "住宿" 与 "环境" 的词根各自重复出现三次（但从不相邻），
    # 以拉高它在原始 BM25（无相邻词组加分）下的词频得分；exact_phrase 里
    # "住宿环境" 只连续出现一次、且全文很短。经实测验证：在没有相邻词组加分
    # 时，scattered 凭更高的原始词频反而赢过 exact_phrase（raw BM25 分数
    # scattered > exact_phrase）；只有相邻词组加分真正识别出 "住宿环境" 在
    # exact_phrase 里是连续短语并给予加分时，exact_phrase 才能反超排到
    # 第一——这样测试才是在验证加分机制本身，而不是恰好搭了长度归一化或
    # 词频的便车。
    scattered = Document(
        page_content="住宿条件不错，选择也很多，周边环境优美，绿化很好，环境保护做得不错，交通也算方便。",
        metadata={"chunk_id": "scattered"},
    )
    exact_phrase = Document(
        page_content="住宿环境干净整洁，适合居住。",
        metadata={"chunk_id": "exact"},
    )
    retriever = HybridRetriever(None, [scattered, exact_phrase], k=2)

    result = retriever.retrieve("住宿环境")

    assert result[0].metadata["chunk_id"] == "exact"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_query_weighting.py -v`
Expected: 全部 FAIL（`app.rag.synonyms` 模块不存在 / 标题加权和同义词召回还没实现）。

- [ ] **Step 3: 新增同义词词典**

创建 `app/rag/synonyms.py`：

```python
"""旅行领域同义词表：用于查询构造阶段的召回扩展，不参与字段加权判断。"""

SYNONYM_GROUPS: list[set[str]] = [
    {"宾馆", "酒店", "住宿"},
    {"景点", "景区", "游览地"},
    {"美食", "小吃", "餐馆"},
    {"交通", "出行", "班次"},
    {"天气", "气候"},
]

_SYNONYM_LOOKUP: dict[str, set[str]] = {}
for _group in SYNONYM_GROUPS:
    for _term in _group:
        _SYNONYM_LOOKUP[_term] = _group - {_term}


def expand_synonyms(terms: list[str]) -> list[str]:
    """返回命中同义词表的词对应的近义词（不含原词），仅用于扩大召回。"""
    expanded: list[str] = []
    for term in terms:
        expanded.extend(sorted(_SYNONYM_LOOKUP.get(term, set())))
    return expanded
```

- [ ] **Step 4: 在 `HybridRetriever` 里接入标题加权、同义词扩展、相邻词组加分**

替换 `app/rag/retriever.py` 全文为：

```python
"""
混合检索器：BM25（标题加权 + 同义词扩展 + 相邻词组加分）+ Dense + RRF 融合
"""
from typing import List, Tuple
from langchain_core.documents import Document
from langchain_chroma import Chroma
from rank_bm25 import BM25Okapi
import jieba
from app.utils.logger import app_logger
from app.rag.identifiers import chunk_id
from app.rag.reranker import RelevanceReranker
from app.rag.synonyms import expand_synonyms


TITLE_WEIGHT_REPEAT = 3
BIGRAM_BONUS = 0.5


class HybridRetriever:
    """
    混合检索器

    结合：
    - BM25（关键词匹配，标题字段加权 + 同义词扩展召回 + 相邻词组加分）
    - Dense（语义相似度，检索失败时自动降级为跳过）
    - RRF（倒数排名融合）
    """

    def __init__(
            self,
            vectorstore: Chroma | None,
            documents: List[Document],
            k: int = 5,
            parent_documents: List[Document] | None = None,
            reranker: RelevanceReranker | None = None,
    ):
        self.vectorstore = vectorstore
        self.documents = documents
        self.k = k
        self.parent_documents = {
            str(doc.metadata.get("parent_id")): doc
            for doc in (parent_documents or [])
            if doc.metadata.get("parent_id")
        }
        self.reranker = reranker
        self._init_bm25()

    def _init_bm25(self):
        """初始化 BM25 索引：section_title 命中的词会重复计入，获得更高权重。"""
        app_logger.info("初始化 BM25 索引...")
        tokenized_docs = [self._tokenize_document(doc) for doc in self.documents]
        self.bm25 = BM25Okapi(tokenized_docs)
        app_logger.info("✅ BM25 索引初始化完成")

    @staticmethod
    def _tokenize_document(document: Document) -> List[str]:
        body_tokens = list(jieba.cut(document.page_content))
        section_title = str(document.metadata.get("section_title", "")).strip()
        if not section_title:
            return body_tokens
        title_tokens = list(jieba.cut(section_title)) * TITLE_WEIGHT_REPEAT
        return title_tokens + body_tokens

    def _bigram_scores(self, query_tokens: List[str]) -> List[float]:
        bigrams = ["".join(pair) for pair in zip(query_tokens, query_tokens[1:])]
        if not bigrams:
            return [0.0] * len(self.documents)
        return [
            BIGRAM_BONUS * sum(1 for bigram in bigrams if bigram in document.page_content)
            for document in self.documents
        ]

    def retrieve(
            self,
            query: str,
            *,
            metadata_filter: dict | None = None,
    ) -> List[Document]:
        """
        混合检索

        流程：
        1. BM25 检索 top-k（同义词扩展召回 + 相邻词组加分）
        2. Dense 检索 top-k（失败时自动降级，不抛出异常）
        3. RRF 融合
        4. 返回融合后的 top-k
        """
        # BM25 检索
        query_tokens = list(jieba.cut(query))
        expanded_tokens = query_tokens + expand_synonyms(query_tokens)
        bm25_raw_scores = self.bm25.get_scores(expanded_tokens)
        bigram_bonus = self._bigram_scores(query_tokens)
        bm25_scores = [score + bigram_bonus[i] for i, score in enumerate(bm25_raw_scores)]
        bm25_top_indices = sorted(
            range(len(bm25_scores)),
            key=lambda i: bm25_scores[i],
            reverse=True
        )[:self.k * 2]
        bm25_docs = [(self.documents[i], bm25_scores[i]) for i in bm25_top_indices]
        app_logger.debug(f"BM25 检索到 {len(bm25_docs)} 个候选")

        # Dense 检索
        dense_docs = (
            self.vectorstore.similarity_search_with_score(query, k=self.k * 2)
            if self.vectorstore is not None
            else []
        )
        app_logger.debug(f"Dense 检索到 {len(dense_docs)} 个候选")

        # RRF 融合
        fused_docs = self._rrf_fusion(bm25_docs, dense_docs, k=60)
        app_logger.info(f"✅ 混合检索完成，返回 {len(fused_docs)} 个结果")
        if self.reranker:
            fused_docs = self.reranker.rerank(query, fused_docs, top_k=self.k)
        return self._resolve_parent_documents(fused_docs)

    def _rrf_fusion(
            self,
            bm25_docs: List[Tuple[Document, float]],
            dense_docs: List[Tuple[Document, float]],
            k: int = 60
    ) -> List[Document]:
        """倒数排名融合（RRF）"""
        scores = {}

        for rank, (doc, _) in enumerate(bm25_docs, 1):
            doc_id = chunk_id(doc)
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)

        for rank, (doc, _) in enumerate(dense_docs, 1):
            doc_id = chunk_id(doc)
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (k + rank)

        all_docs = {chunk_id(doc): doc for doc, _ in bm25_docs + dense_docs}
        sorted_docs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [all_docs[doc_id] for doc_id, _ in sorted_docs[:self.k]]

    def _resolve_parent_documents(self, documents: List[Document]) -> List[Document]:
        if not self.parent_documents:
            return documents[:self.k]

        resolved = []
        seen = set()
        for document in documents:
            parent_id = str(document.metadata.get("parent_id", ""))
            parent = self.parent_documents.get(parent_id, document)
            key = chunk_id(parent)
            if key not in seen:
                resolved.append(parent)
                seen.add(key)
        return resolved[:self.k]
```

（这一步先给 `retrieve` 加上 `metadata_filter` 关键字参数但暂不使用——Dense 分支的真正接入和 `filter` 透传留给 Task 3，这里先保证签名稳定，`search()`/现有调用方不受影响。）

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_query_weighting.py -v`
Expected: 5 个测试全部 PASS。

- [ ] **Step 6: 跑一遍现有 RAG 回归测试**

Run: `python -m pytest tests/test_phase2_rag.py tests/test_phase2_rag_workers.py tests/test_phase2_mock_rag_e2e.py tests/test_markdown_splitter.py -v`
Expected: 全部 PASS。

- [ ] **Step 7: Commit**

```bash
git add app/rag/synonyms.py app/rag/retriever.py tests/test_query_weighting.py
git commit -m "feat(rag): add title weighting, synonym expansion, and bigram boost to BM25 retrieval"
```

---

### Task 3: Dense 检索接入（本地 Ollama + 单一持久化向量库）

**Files:**
- Create: `app/rag/local_embeddings.py`
- Create: `scripts/build_vectorstore.py`
- Modify: `app/rag/vectorstore.py`
- Modify: `app/rag/retriever.py`
- Modify: `app/agents/workers/local_knowledge.py`
- Test: `tests/test_dense_retrieval.py`, `tests/test_local_knowledge_dense_wiring.py`, `tests/test_build_vectorstore.py`

**Interfaces:**
- Consumes: `HybridRetriever.retrieve(query, *, metadata_filter=None)`（Task 2 产出的签名）；`ParentDocumentSplitter.split_documents`（Task 1）。
- Produces: `app.rag.local_embeddings.get_ollama_embeddings(*, base_url=..., model=...) -> OpenAIEmbeddings`；`VectorStoreManager.__init__` 新增 `embeddings: Embeddings | None = None` 参数；`scripts/build_vectorstore.build_vectorstore(*, document_manager=None, persist_directory="data/vectorstore") -> None` 和 `LOCAL_MOCK_COLLECTION` 常量；`LocalKnowledgeService.__init__` 新增 `vectorstore: Chroma | None = None` 参数（供测试注入）。

- [ ] **Step 1: 写失败的测试（`HybridRetriever` 的 filter 透传与降级）**

创建 `tests/test_dense_retrieval.py`：

```python
from langchain_core.documents import Document

from app.rag.retriever import HybridRetriever


class _FakeVectorstore:
    def __init__(self, results=None, error: Exception | None = None):
        self._results = results or []
        self._error = error
        self.last_filter = None

    def similarity_search_with_score(self, query, k, filter=None):
        self.last_filter = filter
        if self._error is not None:
            raise self._error
        return self._results


def test_retrieve_passes_metadata_filter_through_to_vectorstore():
    doc = Document(page_content="宽窄巷子历史街区", metadata={"chunk_id": "a"})
    fake_vectorstore = _FakeVectorstore(results=[(doc, 0.1)])
    retriever = HybridRetriever(fake_vectorstore, [doc], k=1)

    retriever.retrieve(
        "宽窄巷子",
        metadata_filter={"$and": [{"city": "成都"}, {"category": "attractions"}]},
    )

    assert fake_vectorstore.last_filter == {"$and": [{"city": "成都"}, {"category": "attractions"}]}


def test_retrieve_degrades_to_bm25_only_when_dense_search_raises():
    doc = Document(page_content="宽窄巷子历史街区", metadata={"chunk_id": "a"})
    other = Document(page_content="武侯祠博物馆", metadata={"chunk_id": "b"})
    fake_vectorstore = _FakeVectorstore(error=ConnectionError("ollama unreachable"))
    retriever = HybridRetriever(fake_vectorstore, [doc, other], k=2)

    result = retriever.retrieve("宽窄巷子")

    assert [item.metadata["chunk_id"] for item in result] == ["a", "b"]


def test_vectorstore_manager_uses_injected_embeddings_instead_of_dashscope(tmp_path):
    from app.rag.vectorstore import VectorStoreManager

    sentinel = object()
    manager = VectorStoreManager(persist_directory=str(tmp_path), embeddings=sentinel)

    assert manager.embeddings is sentinel


def test_get_ollama_embeddings_points_at_local_ollama_openai_compatible_endpoint():
    from app.rag.local_embeddings import get_ollama_embeddings

    embeddings = get_ollama_embeddings()

    assert embeddings.openai_api_base == "http://127.0.0.1:11434/v1"
    assert embeddings.model == "qwen3-embedding:4b"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_dense_retrieval.py -v`
Expected: 全部 FAIL（`filter` 还没透传给 vectorstore、Dense 异常没有被捕获、
`VectorStoreManager` 还不接受 `embeddings` 参数、`app.rag.local_embeddings`
模块不存在）。

- [ ] **Step 3: 新增本地 Ollama embedding 客户端**

创建 `app/rag/local_embeddings.py`：

```python
"""本地 Ollama Embedding 客户端：复用 OpenAI 兼容接口，不依赖外部 API Key。"""

from __future__ import annotations

from langchain_openai import OpenAIEmbeddings

OLLAMA_BASE_URL = "http://127.0.0.1:11434/v1"
OLLAMA_EMBEDDING_MODEL = "qwen3-embedding:4b"
LOCAL_MOCK_COLLECTION = "local_mock_dense"


def get_ollama_embeddings(
    *,
    base_url: str = OLLAMA_BASE_URL,
    model: str = OLLAMA_EMBEDDING_MODEL,
) -> OpenAIEmbeddings:
    """构造指向本地 Ollama 的 embedding 客户端。

    `check_embedding_ctx_length=False` 是必需的：langchain-openai 默认会用
    tiktoken 按已知 OpenAI 模型名做 token 截断，本地模型名不在其列表里会
    直接报错，关闭这项检查后按原始文本发送。
    """
    return OpenAIEmbeddings(
        base_url=base_url,
        model=model,
        api_key="ollama-local-placeholder",
        check_embedding_ctx_length=False,
    )
```

- [ ] **Step 4: 让 `VectorStoreManager` 支持注入 embedding 客户端**

在 `app/rag/vectorstore.py` 中，修改 import 和 `__init__`：

```python
"""
向量数据库管理
"""
from typing import List
from pathlib import Path
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_chroma import Chroma
from langchain_community.embeddings import DashScopeEmbeddings
from app.config import settings
from app.utils.logger import app_logger


class VectorStoreManager:
    """向量数据库管理器"""

    def __init__(
        self,
        persist_directory: str = "data/vectorstore",
        collection_name: str = "travel_guides",
        embeddings: Embeddings | None = None,
    ):
        self.persist_directory = Path(persist_directory)
        self.collection_name = collection_name
        self.persist_directory.mkdir(parents=True, exist_ok=True)

        self.embeddings = embeddings or DashScopeEmbeddings(
            model="text-embedding-v2",
            dashscope_api_key=settings.dashscope_api_key
        )
        self.vectorstore = None

    def create_vectorstore(self, documents: List[Document]) -> Chroma:
        """创建向量数据库"""
        app_logger.info(f"创建向量数据库（{len(documents)} 个文档）...")
        self.vectorstore = Chroma.from_documents(
            documents=documents,
            ids=[str(doc.metadata["chunk_id"]) for doc in documents],
            embedding=self.embeddings,
            persist_directory=str(self.persist_directory),
            collection_name=self.collection_name
        )
        app_logger.info("✅ 向量数据库创建完成")
        return self.vectorstore

    def load_vectorstore(self) -> Chroma:
        """加载已有向量数据库"""
        app_logger.info("加载向量数据库...")
        self.vectorstore = Chroma(
            persist_directory=str(self.persist_directory),
            embedding_function=self.embeddings,
            collection_name=self.collection_name
        )
        app_logger.info("✅ 向量数据库加载完成")
        return self.vectorstore

    def get_vectorstore(self) -> Chroma:
        if self.vectorstore is None:
            try:
                return self.load_vectorstore()
            except Exception:
                app_logger.warning("⚠️ 向量数据库不存在，需先运行 scripts/init_rag.py")
                raise RuntimeError("向量数据库未初始化，请先运行 scripts/init_rag.py")
        return self.vectorstore
```

（`embeddings=None` 时行为和之前完全一样，`scripts/init_rag.py` 不受影响。）

- [ ] **Step 5: 让 `HybridRetriever.retrieve` 透传 filter 并在 Dense 检索失败时降级**

在 `app/rag/retriever.py` 中，把 `retrieve` 方法里的 Dense 检索部分替换为：

```python
    def retrieve(
            self,
            query: str,
            *,
            metadata_filter: dict | None = None,
    ) -> List[Document]:
        """
        混合检索

        流程：
        1. BM25 检索 top-k（同义词扩展召回 + 相邻词组加分）
        2. Dense 检索 top-k（失败时自动降级，不抛出异常）
        3. RRF 融合
        4. 返回融合后的 top-k
        """
        # BM25 检索
        query_tokens = list(jieba.cut(query))
        expanded_tokens = query_tokens + expand_synonyms(query_tokens)
        bm25_raw_scores = self.bm25.get_scores(expanded_tokens)
        bigram_bonus = self._bigram_scores(query_tokens)
        bm25_scores = [score + bigram_bonus[i] for i, score in enumerate(bm25_raw_scores)]
        bm25_top_indices = sorted(
            range(len(bm25_scores)),
            key=lambda i: bm25_scores[i],
            reverse=True
        )[:self.k * 2]
        bm25_docs = [(self.documents[i], bm25_scores[i]) for i in bm25_top_indices]
        app_logger.debug(f"BM25 检索到 {len(bm25_docs)} 个候选")

        # Dense 检索（失败时降级为空候选，不影响 BM25 结果）
        dense_docs: List[Tuple[Document, float]] = []
        if self.vectorstore is not None:
            try:
                dense_docs = self.vectorstore.similarity_search_with_score(
                    query, k=self.k * 2, filter=metadata_filter
                )
            except Exception as exc:
                app_logger.warning(f"Dense 检索失败，本次查询退化为纯 BM25：{type(exc).__name__}: {exc}")
                dense_docs = []
        app_logger.debug(f"Dense 检索到 {len(dense_docs)} 个候选")

        # RRF 融合
        fused_docs = self._rrf_fusion(bm25_docs, dense_docs, k=60)
        app_logger.info(f"✅ 混合检索完成，返回 {len(fused_docs)} 个结果")
        if self.reranker:
            fused_docs = self.reranker.rerank(query, fused_docs, top_k=self.k)
        return self._resolve_parent_documents(fused_docs)
```

（其余方法 `_init_bm25`/`_tokenize_document`/`_bigram_scores`/`_rrf_fusion`/`_resolve_parent_documents` 保持 Task 2 写好的样子不变。）

- [ ] **Step 6: 新增离线构建脚本**

创建 `scripts/build_vectorstore.py`：

```python
"""离线构建本地 Dense 向量库：用本地 Ollama embedding 把成都模拟资料转成向量，
写入一个持久化 Chroma collection，供 LocalKnowledgeService 的 Dense 检索加载。

不在 FastAPI 请求路径上运行，可重复执行（覆盖同一 collection）。运行前需先
启动本地 Ollama 并确保已拉取 qwen3-embedding:4b 模型：
    ollama pull qwen3-embedding:4b
运行方式：
    python scripts/build_vectorstore.py
"""

from __future__ import annotations

from app.rag.document_loader import DocumentManager
from app.rag.local_embeddings import LOCAL_MOCK_COLLECTION, get_ollama_embeddings
from app.rag.text_splitter import ParentDocumentSplitter
from app.rag.vectorstore import VectorStoreManager
from app.utils.logger import app_logger


def build_vectorstore(
    *,
    document_manager: DocumentManager | None = None,
    persist_directory: str = "data/vectorstore",
) -> None:
    document_manager = document_manager or DocumentManager()
    documents = document_manager.load_all_documents()
    if not documents:
        app_logger.warning("未找到文档，跳过向量库构建。")
        return

    _, children = ParentDocumentSplitter().split_documents(documents)
    if not children:
        app_logger.warning("切分后没有子文档，跳过向量库构建。")
        return

    manager = VectorStoreManager(
        persist_directory=persist_directory,
        collection_name=LOCAL_MOCK_COLLECTION,
        embeddings=get_ollama_embeddings(),
    )
    manager.create_vectorstore(children)
    app_logger.info(
        f"✅ 本地向量库构建完成：{len(children)} 个子文档，"
        f"collection={LOCAL_MOCK_COLLECTION}，目录={persist_directory}"
    )


def main() -> None:
    build_vectorstore()


if __name__ == "__main__":
    main()
```

- [ ] **Step 7: 让 `LocalKnowledgeService` 加载并复用共享向量库**

替换 `app/agents/workers/local_knowledge.py` 全文为：

```python
"""本地静态知识的 Hybrid RAG 查询入口。"""

from functools import lru_cache

from langchain_chroma import Chroma
from langchain_core.documents import Document

from app.rag.document_loader import DocumentManager
from app.rag.evidence import evidence_from_document
from app.rag.local_embeddings import LOCAL_MOCK_COLLECTION, get_ollama_embeddings
from app.rag.reranker import RelevanceReranker
from app.rag.retriever import HybridRetriever
from app.rag.text_splitter import ParentDocumentSplitter
from app.rag.vectorstore import VectorStoreManager
from app.schemas.planning import Evidence, TaskType
from app.utils.logger import app_logger


class LocalKnowledgeService:
    def __init__(
        self,
        documents: list[Document] | None = None,
        vectorstore: Chroma | None = None,
    ):
        self.documents = documents if documents is not None else DocumentManager().load_all_documents()
        self.vectorstore = vectorstore if vectorstore is not None else self._load_vectorstore()
        self.retriever = self._build_retriever(self.documents, self.vectorstore)

    @staticmethod
    def _load_vectorstore() -> Chroma | None:
        try:
            manager = VectorStoreManager(
                collection_name=LOCAL_MOCK_COLLECTION,
                embeddings=get_ollama_embeddings(),
            )
            return manager.load_vectorstore()
        except Exception as exc:
            app_logger.warning(f"本地向量库不可用，Dense 检索退化为跳过：{type(exc).__name__}: {exc}")
            return None

    @staticmethod
    def _build_retriever(documents: list[Document], vectorstore: Chroma | None) -> HybridRetriever | None:
        parents, children = ParentDocumentSplitter().split_documents(documents)
        if not children:
            return None
        return HybridRetriever(
            vectorstore=vectorstore,
            documents=children,
            parent_documents=parents,
            reranker=RelevanceReranker(),
            k=4,
        )

    def search(self, query: str) -> list[Evidence]:
        if self.retriever is None:
            return []
        return [evidence_from_document(document) for document in self.retriever.retrieve(query)]

    def search_destination(
        self,
        destination: str,
        category: TaskType,
        query: str,
    ) -> list[Evidence]:
        normalized_destination = destination.strip().casefold()
        normalized_category = category.strip().casefold()
        documents = [
            document
            for document in self.documents
            if str(document.metadata.get("city", "")).strip().casefold() == normalized_destination
            and str(document.metadata.get("category", "")).strip().casefold() == normalized_category
        ]
        if not documents:
            return []

        retrieval_query = f"{destination} {category} {query}"
        retriever = self._build_retriever(documents, self.vectorstore)
        if retriever is None:
            return []
        metadata_filter = (
            {"$and": [{"city": destination}, {"category": category}]}
            if self.vectorstore is not None
            else None
        )
        return [
            evidence_from_document(document)
            for document in retriever.retrieve(retrieval_query, metadata_filter=metadata_filter)
        ]


@lru_cache(maxsize=1)
def get_local_knowledge_service() -> LocalKnowledgeService:
    return LocalKnowledgeService()


def load_destination_evidence(destination: str, topic: str) -> list[Evidence]:
    items = get_local_knowledge_service().search(f"{destination} {topic}")
    return [
        item
        for item in items
        if destination in item.content or destination in item.source
    ]
```

- [ ] **Step 8: 运行测试确认通过**

Run: `python -m pytest tests/test_dense_retrieval.py -v`
Expected: 4 个测试全部 PASS。

- [ ] **Step 9: 写 `LocalKnowledgeService` 的 filter 透传测试**

创建 `tests/test_local_knowledge_dense_wiring.py`：

```python
from langchain_core.documents import Document

from app.agents.workers.local_knowledge import LocalKnowledgeService


class _RecordingVectorstore:
    def __init__(self):
        self.calls: list[dict] = []

    def similarity_search_with_score(self, query, k, filter=None):
        self.calls.append({"query": query, "filter": filter})
        return []


def test_search_destination_filters_dense_search_by_city_and_category():
    documents = [
        Document(
            page_content="位于成华区。是熊猫文化主题下的代表性自然教育地点。",
            metadata={"city": "成都", "category": "attractions", "chunk_id": "panda"},
        ),
    ]
    vectorstore = _RecordingVectorstore()
    service = LocalKnowledgeService(documents=documents, vectorstore=vectorstore)

    service.search_destination("成都", "attractions", "熊猫基地")

    assert vectorstore.calls
    assert vectorstore.calls[0]["filter"] == {"$and": [{"city": "成都"}, {"category": "attractions"}]}


def test_service_reuses_the_same_vectorstore_instance_across_queries_without_rebuilding():
    documents = [
        Document(
            page_content="位于成华区。",
            metadata={"city": "成都", "category": "attractions", "chunk_id": "panda"},
        ),
        Document(
            page_content="位于青羊区。",
            metadata={"city": "成都", "category": "hotel", "chunk_id": "hotel"},
        ),
    ]
    vectorstore = _RecordingVectorstore()
    service = LocalKnowledgeService(documents=documents, vectorstore=vectorstore)

    service.search_destination("成都", "attractions", "熊猫")
    service.search_destination("成都", "hotel", "住宿")

    assert service.vectorstore is vectorstore
    assert len(vectorstore.calls) == 2
```

Run: `python -m pytest tests/test_local_knowledge_dense_wiring.py -v`
Expected: 2 个测试全部 PASS（新代码已在 Step 7 写好，这一步是补齐覆盖）。

- [ ] **Step 10: 写离线脚本的 opt-in 集成测试**

创建 `tests/test_build_vectorstore.py`：

```python
import os

import pytest

from app.rag.local_embeddings import LOCAL_MOCK_COLLECTION
from scripts.build_vectorstore import build_vectorstore

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("RUN_OLLAMA_TESTS") != "1",
        reason="requires RUN_OLLAMA_TESTS=1 and a running local Ollama with qwen3-embedding:4b pulled",
    ),
]


def test_build_vectorstore_writes_a_queryable_local_collection(tmp_path):
    from app.rag.local_embeddings import get_ollama_embeddings
    from app.rag.vectorstore import VectorStoreManager

    persist_directory = str(tmp_path / "vectorstore")
    build_vectorstore(persist_directory=persist_directory)

    manager = VectorStoreManager(
        persist_directory=persist_directory,
        collection_name=LOCAL_MOCK_COLLECTION,
        embeddings=get_ollama_embeddings(),
    )
    vectorstore = manager.load_vectorstore()

    results = vectorstore.similarity_search_with_score("熊猫基地", k=1)

    assert results
```

Run（默认跳过）: `python -m pytest tests/test_build_vectorstore.py -v`
Expected: 1 个测试 SKIPPED（没有设置 `RUN_OLLAMA_TESTS=1`）。
如果本机已启动 Ollama 并拉取好模型，可以手动运行
`RUN_OLLAMA_TESTS=1 python -m pytest tests/test_build_vectorstore.py -v`
验证真实链路，但这不是本任务默认验证步骤的一部分。

- [ ] **Step 11: 跑一遍现有 RAG 和 Worker 回归测试**

Run: `python -m pytest tests/test_phase2_rag.py tests/test_phase2_rag_workers.py tests/test_phase2_mock_rag_e2e.py tests/test_markdown_splitter.py tests/test_query_weighting.py -v`
Expected: 全部 PASS。

- [ ] **Step 12: Commit**

```bash
git add app/rag/local_embeddings.py app/rag/vectorstore.py app/rag/retriever.py \
  app/agents/workers/local_knowledge.py scripts/build_vectorstore.py \
  tests/test_dense_retrieval.py tests/test_local_knowledge_dense_wiring.py \
  tests/test_build_vectorstore.py
git commit -m "feat(rag): wire Dense retrieval to a shared local Ollama vectorstore with metadata filtering"
```

---

### Task 4: CrossEncoder 重排开关

**Files:**
- Modify: `app/config.py`
- Modify: `app/agents/workers/local_knowledge.py`
- Create: `docs/rag-cross-encoder-setup.md`
- Test: `tests/test_cross_encoder_toggle.py`, `tests/test_cross_encoder_real_model.py`

**Interfaces:**
- Consumes: `app.rag.reranker.CrossEncoderReranker(model_name: str)`（已存在，未改动）；`LocalKnowledgeService._build_retriever`（Task 3 产出）。
- Produces: `settings.enable_cross_encoder_rerank: bool`、`settings.cross_encoder_model: str`；`LocalKnowledgeService._select_reranker() -> RelevanceReranker`。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_cross_encoder_toggle.py`：

```python
from app.agents.workers.local_knowledge import LocalKnowledgeService
from app.config import settings
from app.rag.reranker import CrossEncoderReranker, RelevanceReranker


def test_default_reranker_is_lexical_and_does_not_load_cross_encoder(monkeypatch):
    monkeypatch.setattr(settings, "enable_cross_encoder_rerank", False)

    reranker = LocalKnowledgeService._select_reranker()

    assert type(reranker) is RelevanceReranker


def test_toggle_enabled_selects_cross_encoder_reranker_without_downloading_model(monkeypatch):
    monkeypatch.setattr(settings, "enable_cross_encoder_rerank", True)
    monkeypatch.setattr(settings, "cross_encoder_model", "BAAI/bge-reranker-base")

    reranker = LocalKnowledgeService._select_reranker()

    assert isinstance(reranker, CrossEncoderReranker)
    assert reranker.model_name == "BAAI/bge-reranker-base"
    assert reranker._model is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_cross_encoder_toggle.py -v`
Expected: 全部 FAIL（`settings.enable_cross_encoder_rerank` 不存在，
`LocalKnowledgeService._select_reranker` 不存在）。

- [ ] **Step 3: 加配置项**

在 `app/config.py` 的 `Settings` 类里，`external_max_retries` 字段之后新增：

```python
    # ============== RAG 检索配置 ==============
    enable_cross_encoder_rerank: bool = Field(default=False, alias="ENABLE_CROSS_ENCODER_RERANK")
    cross_encoder_model: str = Field(default="BAAI/bge-reranker-base", alias="CROSS_ENCODER_MODEL")
```

- [ ] **Step 4: 在 `LocalKnowledgeService` 里接入开关**

在 `app/agents/workers/local_knowledge.py` 顶部 import 区新增：

```python
from app.config import settings
from app.rag.reranker import CrossEncoderReranker, RelevanceReranker
```

（替换掉原来的 `from app.rag.reranker import RelevanceReranker` 单独一行。）

把 `_build_retriever` 和其上新增的 `_select_reranker` 改成：

```python
    @staticmethod
    def _select_reranker() -> RelevanceReranker:
        if settings.enable_cross_encoder_rerank:
            return CrossEncoderReranker(settings.cross_encoder_model)
        return RelevanceReranker()

    @staticmethod
    def _build_retriever(documents: list[Document], vectorstore: Chroma | None) -> HybridRetriever | None:
        parents, children = ParentDocumentSplitter().split_documents(documents)
        if not children:
            return None
        return HybridRetriever(
            vectorstore=vectorstore,
            documents=children,
            parent_documents=parents,
            reranker=LocalKnowledgeService._select_reranker(),
            k=4,
        )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_cross_encoder_toggle.py -v`
Expected: 2 个测试全部 PASS。

- [ ] **Step 6: 写 opt-in 的真实模型测试**

创建 `tests/test_cross_encoder_real_model.py`：

```python
import os

import pytest
from langchain_core.documents import Document

from app.rag.reranker import CrossEncoderReranker

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        os.getenv("RUN_CROSS_ENCODER_TESTS") != "1",
        reason="requires RUN_CROSS_ENCODER_TESTS=1 and network access to download the model",
    ),
]


def test_cross_encoder_reranker_downloads_and_scores_real_model():
    reranker = CrossEncoderReranker("BAAI/bge-reranker-base")
    documents = [
        Document(page_content="宽窄巷子是成都著名的历史文化街区。"),
        Document(page_content="今天天气晴朗，气温适宜。"),
    ]

    ranked = reranker.rerank("成都有什么历史街区", documents, top_k=1)

    assert "历史文化街区" in ranked[0].page_content
```

Run（默认跳过）: `python -m pytest tests/test_cross_encoder_real_model.py -v`
Expected: 1 个测试 SKIPPED（没有设置 `RUN_CROSS_ENCODER_TESTS=1`）。

- [ ] **Step 7: 写启用说明文档**

创建 `docs/rag-cross-encoder-setup.md`：

```markdown
# 怎么启用 CrossEncoder 重排

## 这是什么

`app/rag/reranker.py` 里的 `CrossEncoderReranker` 用一个训练过的小模型判断
"查询和候选文档语义上有多相关"，比默认的 `RelevanceReranker`（词频重叠计数）
更能识别同义改写、说法不同但意思一样的情况。默认关闭，因为它需要在本地
下载一个模型文件并占用推理时间。

## 怎么打开

在 `.env` 里加：

```
ENABLE_CROSS_ENCODER_RERANK=true
CROSS_ENCODER_MODEL=BAAI/bge-reranker-base
```

`CROSS_ENCODER_MODEL` 不设置时默认就是 `BAAI/bge-reranker-base`。

## 候选模型

- `BAAI/bge-reranker-base`（默认）：中文效果和模型体积比较均衡，约
  1.1GB，首次加载需要从 HuggingFace 下载。
- `BAAI/bge-reranker-large`：精度更高，体积约 2.2GB，推理速度更慢，适合
  对排序质量要求更高、能接受更高延迟的场景。
- `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`：体积更小（约 470MB）、
  速度更快，但主要面向英文/多语言语料训练，中文效果弱于 bge 系列。

## 资源需求

- 首次调用会从 HuggingFace 下载模型到本地缓存（默认
  `~/.cache/huggingface`），需要网络访问；之后复用本地缓存，不会重复下载。
- 模型加载到内存后，单次查询的重排推理耗时通常在几十到几百毫秒量级
  （取决于候选文档数量和机器算力），比默认的词频重叠打分慢，但比调用
  远程 LLM API 快很多。

## 怎么验证生效

1. 打开开关后跑：`RUN_CROSS_ENCODER_TESTS=1 python -m pytest tests/test_cross_encoder_real_model.py -v`，
   确认模型能正常下载并给出合理的排序结果。
2. 或者直接跑一次真实查询，对比开关打开前后同一个查询返回的
   `Evidence`/chunk 顺序是否发生变化——`RelevanceReranker.rerank` 和
   `CrossEncoderReranker.rerank` 返回的 chunk 都带 `metadata["rerank_score"]`，
   可以直接比较分数分布。
```

- [ ] **Step 8: 跑一遍现有 RAG 和 Worker 回归测试**

Run: `python -m pytest tests/test_phase2_rag.py tests/test_phase2_rag_workers.py tests/test_phase2_mock_rag_e2e.py tests/test_markdown_splitter.py tests/test_query_weighting.py tests/test_dense_retrieval.py tests/test_local_knowledge_dense_wiring.py -v`
Expected: 全部 PASS。

- [ ] **Step 9: Commit**

```bash
git add app/config.py app/agents/workers/local_knowledge.py \
  docs/rag-cross-encoder-setup.md tests/test_cross_encoder_toggle.py \
  tests/test_cross_encoder_real_model.py
git commit -m "feat(rag): add default-off CrossEncoder reranker toggle with setup docs"
```

---

### Task 5: `GraphCommunityService`（图社区检测简化版）

**Files:**
- Create: `app/rag/graph_community.py`
- Test: `tests/test_graph_community.py`

**Interfaces:**
- Consumes: 无（本任务独立于 Task 1-4，不依赖它们的产出）。
- Produces: `CommunityEntity(id: str, name: str)`、`CommunityRelation(from_entity_id: str, to_entity_id: str, relation_type: str)`、`Community(entities: list[CommunityEntity], importance: float, summary: str)`、`GraphCommunityService.build_communities(entities, relations) -> list[Community]`、`get_graph_community_service() -> GraphCommunityService`。真实算法升级路径见 `docs/superpowers/specs/2026-07-24-rag-heavyweight-capabilities-future-design.md`。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_graph_community.py`：

```python
from app.rag.graph_community import CommunityEntity, CommunityRelation, GraphCommunityService


def test_each_entity_becomes_its_own_community_with_equal_importance():
    entities = [
        CommunityEntity(id="1", name="宽窄巷子"),
        CommunityEntity(id="2", name="武侯祠"),
    ]
    relations = [CommunityRelation(from_entity_id="1", to_entity_id="2", relation_type="near")]
    service = GraphCommunityService()

    communities = service.build_communities(entities, relations)

    assert len(communities) == 2
    assert {community.entities[0].name for community in communities} == {"宽窄巷子", "武侯祠"}
    assert all(community.importance == 1.0 for community in communities)


def test_build_communities_handles_empty_entity_list():
    service = GraphCommunityService()

    assert service.build_communities([], []) == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_graph_community.py -v`
Expected: 全部 FAIL（`app.rag.graph_community` 模块不存在）。

- [ ] **Step 3: 实现简化版 `GraphCommunityService`**

创建 `app/rag/graph_community.py`：

```python
"""图社区检测的简化实现：每个实体各自成一个社区，重要度全部相等。

真实的 Leiden 社区检测 + PageRank 重要度升级路径见
docs/superpowers/specs/2026-07-24-rag-heavyweight-capabilities-future-design.md。
本阶段不接入 Worker 或离线构建脚本的主流程，只交付可独立测试的接口。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CommunityEntity:
    """独立于 SQLAlchemy ORM 的轻量实体表示，供社区检测使用。"""

    id: str
    name: str


@dataclass(frozen=True)
class CommunityRelation:
    from_entity_id: str
    to_entity_id: str
    relation_type: str


@dataclass
class Community:
    entities: list[CommunityEntity]
    importance: float
    summary: str


class GraphCommunityService:
    def build_communities(
        self,
        entities: list[CommunityEntity],
        relations: list[CommunityRelation],
    ) -> list[Community]:
        # relations 暂未使用：简化版不做真实图聚类，接口先接受它以匹配未来
        # 真实算法（需要遍历关系构图）的签名，避免升级时改调用方。
        return [
            Community(entities=[entity], importance=1.0, summary=entity.name)
            for entity in entities
        ]


def get_graph_community_service() -> GraphCommunityService:
    return GraphCommunityService()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_graph_community.py -v`
Expected: 2 个测试全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/rag/graph_community.py tests/test_graph_community.py
git commit -m "feat(rag): add simplified GraphCommunityService scaffold for future Leiden upgrade"
```

---

### Task 6: `RaptorIndexer`（摘要树简化版）

**Files:**
- Create: `app/rag/raptor.py`
- Test: `tests/test_raptor_indexer.py`

**Interfaces:**
- Consumes: 无（独立于其他任务，只依赖 `langchain_core.documents.Document` 和 `app/rag/identifiers.py` 已有的 `stable_hash`）。
- Produces: `RaptorIndexer.build_tree(documents: list[Document]) -> list[Document]`、`get_raptor_indexer() -> RaptorIndexer`。真实算法升级路径见 `docs/superpowers/specs/2026-07-24-rag-heavyweight-capabilities-future-design.md`。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_raptor_indexer.py`：

```python
from langchain_core.documents import Document

from app.rag.raptor import RaptorIndexer


def test_build_tree_creates_one_summary_per_document_group():
    chunks = [
        Document(page_content="宽窄巷子是历史街区。", metadata={"document_id": "doc-a", "chunk_id": "a1"}),
        Document(page_content="宽窄巷子适合步行游览。", metadata={"document_id": "doc-a", "chunk_id": "a2"}),
        Document(page_content="武侯祠是博物馆与遗址。", metadata={"document_id": "doc-b", "chunk_id": "b1"}),
    ]
    indexer = RaptorIndexer()

    summaries = indexer.build_tree(chunks)

    assert len(summaries) == 2
    assert all(summary.metadata["is_raptor_summary"] for summary in summaries)
    document_ids = {summary.metadata["document_id"] for summary in summaries}
    assert document_ids == {"doc-a", "doc-b"}


def test_build_tree_does_not_recurse_or_mutate_original_chunks():
    chunk = Document(page_content="宽窄巷子是历史街区。", metadata={"document_id": "doc-a", "chunk_id": "a1"})
    indexer = RaptorIndexer()

    summaries = indexer.build_tree([chunk])

    assert chunk.metadata.get("is_raptor_summary") is None
    assert len(summaries) == 1
    assert summaries[0].page_content == "宽窄巷子是历史街区。"[:80]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_raptor_indexer.py -v`
Expected: 全部 FAIL（`app.rag.raptor` 模块不存在）。

- [ ] **Step 3: 实现简化版 `RaptorIndexer`**

创建 `app/rag/raptor.py`：

```python
"""RAPTOR 摘要树的简化实现：按 document_id 分组，每组生成一个非 LLM 的占位
摘要 chunk，不做真实的 UMAP/GMM 递归聚类。真实算法升级路径见
docs/superpowers/specs/2026-07-24-rag-heavyweight-capabilities-future-design.md。
本阶段不接入检索主链路，只交付可独立测试的接口。
"""

from __future__ import annotations

from langchain_core.documents import Document

from app.rag.identifiers import stable_hash

SUMMARY_PREVIEW_LENGTH = 80


class RaptorIndexer:
    def build_tree(self, documents: list[Document]) -> list[Document]:
        groups: dict[str, list[Document]] = {}
        for document in documents:
            document_id = str(document.metadata.get("document_id", ""))
            groups.setdefault(document_id, []).append(document)

        summaries: list[Document] = []
        for document_id, group in groups.items():
            if not document_id:
                continue
            preview = group[0].page_content[:SUMMARY_PREVIEW_LENGTH]
            summary_metadata = dict(group[0].metadata)
            summary_metadata["is_raptor_summary"] = True
            summary_metadata["chunk_id"] = stable_hash(document_id, "raptor-summary")
            summaries.append(Document(page_content=preview, metadata=summary_metadata))

        return summaries


def get_raptor_indexer() -> RaptorIndexer:
    return RaptorIndexer()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_raptor_indexer.py -v`
Expected: 2 个测试全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/rag/raptor.py tests/test_raptor_indexer.py
git commit -m "feat(rag): add simplified RaptorIndexer scaffold for future recursive clustering upgrade"
```

---

### Task 7: `CitationAnnotator`（逐句溯源简化版）

**Files:**
- Create: `app/rag/citation.py`
- Test: `tests/test_citation_annotator.py`

**Interfaces:**
- Consumes: `app.schemas.planning.Evidence`（已有）。
- Produces: `AnnotatedAnswer(text: str, sources: list[Evidence])`、`CitationAnnotator.annotate(answer: str, evidence: list[Evidence]) -> AnnotatedAnswer`、`get_citation_annotator() -> CitationAnnotator`。真实算法升级路径见 `docs/superpowers/specs/2026-07-24-rag-heavyweight-capabilities-future-design.md`。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_citation_annotator.py`：

```python
from app.rag.citation import CitationAnnotator
from app.schemas.planning import Evidence


def test_annotate_attaches_all_evidence_as_the_answer_sources():
    evidence = [
        Evidence(content="宽窄巷子位于青羊区。", source="attractions/chengdu.md"),
        Evidence(content="武侯祠位于武侯区。", source="attractions/chengdu.md"),
    ]
    annotator = CitationAnnotator()

    result = annotator.annotate("成都值得去宽窄巷子和武侯祠。", evidence)

    assert result.text == "成都值得去宽窄巷子和武侯祠。"
    assert result.sources == evidence


def test_annotate_handles_empty_evidence_list():
    annotator = CitationAnnotator()

    result = annotator.annotate("暂无可用证据。", [])

    assert result.sources == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_citation_annotator.py -v`
Expected: 全部 FAIL（`app.rag.citation` 模块不存在）。

- [ ] **Step 3: 实现简化版 `CitationAnnotator`**

创建 `app/rag/citation.py`：

```python
"""逐句溯源的简化实现：整段回答统一标注为传入证据的来源列表，不做逐句相似度
匹配。真实算法升级路径见
docs/superpowers/specs/2026-07-24-rag-heavyweight-capabilities-future-design.md。
本阶段不接入生成后处理主链路——当前系统的证据溯源已经通过 Evidence/is_mock
在结构化分析阶段实现，这里只交付可独立测试的接口，为未来"更自然语言
生成"场景预留。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.planning import Evidence


@dataclass
class AnnotatedAnswer:
    text: str
    sources: list[Evidence]


class CitationAnnotator:
    def annotate(self, answer: str, evidence: list[Evidence]) -> AnnotatedAnswer:
        return AnnotatedAnswer(text=answer, sources=list(evidence))


def get_citation_annotator() -> CitationAnnotator:
    return CitationAnnotator()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_citation_annotator.py -v`
Expected: 2 个测试全部 PASS。

- [ ] **Step 5: 跑一遍全量回归测试**

Run: `python -m pytest -q`
Expected: 全部 PASS（新增 opt-in 测试 SKIPPED），无 FAILED。

- [ ] **Step 6: Commit**

```bash
git add app/rag/citation.py tests/test_citation_annotator.py
git commit -m "feat(rag): add simplified CitationAnnotator scaffold for future sentence-level attribution"
```
