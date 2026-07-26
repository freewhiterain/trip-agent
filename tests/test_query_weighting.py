from langchain_core.documents import Document

from app.rag.reranker import RelevanceReranker
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
    # 词频的便车。（原始写法里 exact_phrase 特意写得更长以期触发长度归一化
    # 惩罚，但实测该写法下 raw BM25 已经偏向 exact_phrase，无法证明加分
    # 机制本身在起作用，因此改用这组重复词频的写法。）
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


def test_exact_term_match_outranks_synonym_only_match():
    # 同义词扩展必须降权，否则 "宾馆" 与 "酒店" 权重相同。这里 synonym_only
    # 特意把同义词 "宾馆" 重复三次以抬高其词频，而 exact 只出现一次 "酒店"。
    # 若扩展词与原始查询词等权（改动前的行为），synonym_only 会凭更高词频
    # 反超；只有扩展词被 SYNONYM_WEIGHT 降权后 exact 才能保持第一。
    #
    # decoy 是必需的第三篇文档：命中词只出现在 1/2 篇文档时 rank_bm25 的
    # IDF 精确为 0，会把全部词频信号乘没。
    exact = Document(page_content="市中心酒店交通便利。", metadata={"chunk_id": "exact"})
    synonym_only = Document(
        page_content="宾馆环境安静，宾馆价格实惠，宾馆位置也好。",
        metadata={"chunk_id": "synonym"},
    )
    decoy = Document(page_content="熊猫基地全年开放。", metadata={"chunk_id": "decoy"})
    retriever = HybridRetriever(None, [exact, synonym_only, decoy], k=3)

    result = retriever.retrieve("酒店")

    assert result[0].metadata["chunk_id"] == "exact"
    # 同义词仍要能召回，只是排在精确命中之后。
    assert [doc.metadata["chunk_id"] for doc in result[:2]] == ["exact", "synonym"]


def test_reranker_can_promote_a_candidate_outside_the_bm25_top_k():
    # 改动前 RRF 在融合处就截断到 k，重排器只能对这 k 篇调序、无法把
    # 第 k+1 篇提上来。这里让 BM25 与重排器的偏好真正冲突：
    #   partial 只覆盖 1/3 查询词（"门票"）但词频极高、文档极短，BM25 排第一；
    #   full 覆盖全部三个查询词但每词各一次且被填充文本稀释，BM25 排第二。
    # 重排器用的是词项覆盖率，因此偏好 full（1.0）而非 partial（0.333）。
    # k=1 时：若融合仍提前截断，重排器只会看到 partial，返回 partial；
    # 只有保留宽候选池后 full 才进得了重排输入并被提为第一。
    #
    # noise 的作用是压低 "熊猫"/"基地" 的 IDF，让只命中 "门票" 的 partial
    # 能在 BM25 上真正超过覆盖全部词的 full；措辞特意避免出现连续的
    # "熊猫基地"，否则 noise 会拿到相邻词组加分而挤掉 full。
    noise = [
        Document(
            page_content=f"熊猫很多，基地第{index}片区绿化良好。",
            metadata={"chunk_id": f"noise-{index}"},
        )
        for index in range(6)
    ]
    partial = Document(page_content="门票" * 10 + "说明。", metadata={"chunk_id": "partial"})
    full = Document(
        page_content="熊猫很可爱，基地面积大，门票要预约。" + "园区导览讲解安排若干。" * 6,
        metadata={"chunk_id": "full"},
    )
    retriever = HybridRetriever(
        None,
        [partial, full, *noise],
        k=1,
        reranker=RelevanceReranker(),
    )

    result = retriever.retrieve("熊猫基地门票")

    assert [doc.metadata["chunk_id"] for doc in result] == ["full"]


def test_parent_resolution_backfills_to_k_when_children_share_a_parent():
    # 两个子块共享同一父文档，折叠后只剩 1 篇。改动前按 [:k] 返回会让结果
    # 缩水到 1；补齐机制应继续消费候选，把第二个父文档也带上，凑满 k=2。
    shared_parent = Document(
        page_content="宽窄巷子完整介绍。",
        metadata={"parent_id": "p1", "chunk_id": "p1"},
    )
    other_parent = Document(
        page_content="熊猫基地完整介绍。",
        metadata={"parent_id": "p2", "chunk_id": "p2"},
    )
    children = [
        Document(page_content="宽窄巷子位于青羊区。", metadata={"chunk_id": "c1", "parent_id": "p1"}),
        Document(page_content="宽窄巷子适合散步。", metadata={"chunk_id": "c2", "parent_id": "p1"}),
        Document(page_content="熊猫基地位于成华区。", metadata={"chunk_id": "c3", "parent_id": "p2"}),
    ]
    retriever = HybridRetriever(
        None,
        children,
        k=2,
        parent_documents=[shared_parent, other_parent],
    )

    result = retriever.retrieve("宽窄巷子")

    assert len(result) == 2
    assert {doc.metadata["parent_id"] for doc in result} == {"p1", "p2"}


def test_parent_documents_carry_the_child_rerank_score():
    # rerank_score 由重排器写在子块副本上；换成父文档后必须显式传递，
    # 否则下游拿不到相关性分。
    parent = Document(
        page_content="宽窄巷子完整介绍。",
        metadata={"parent_id": "p1", "chunk_id": "p1"},
    )
    child = Document(
        page_content="宽窄巷子位于青羊区。",
        metadata={"chunk_id": "c1", "parent_id": "p1"},
    )
    retriever = HybridRetriever(
        None,
        [child],
        k=1,
        parent_documents=[parent],
        reranker=RelevanceReranker(),
    )

    result = retriever.retrieve("宽窄巷子")

    assert result[0].metadata["parent_id"] == "p1"
    assert "rerank_score" in result[0].metadata
    # 父文档对象本身不能被就地污染。
    assert "rerank_score" not in parent.metadata
