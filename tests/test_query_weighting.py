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
