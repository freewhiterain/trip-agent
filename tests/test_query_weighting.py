from langchain_core.documents import Document

from app.rag.retriever import HybridRetriever
from app.rag.synonyms import expand_synonyms


def test_expand_synonyms_returns_group_members_without_original_term():
    expanded = expand_synonyms(["酒店"])
    assert set(expanded) == {"宾馆", "住宿"}


def test_expand_synonyms_ignores_terms_outside_the_dictionary():
    assert expand_synonyms(["熊猫"]) == []


def test_title_weighted_document_outranks_document_without_matching_title():
    shared_body = "位于城市东北部，环境优美，适合家庭游玩。"
    titled = Document(
        page_content=shared_body,
        metadata={"chunk_id": "titled", "section_title": "熊猫基地"},
    )
    untitled = Document(
        page_content=shared_body,
        metadata={"chunk_id": "untitled", "section_title": "宽窄巷子"},
    )
    retriever = HybridRetriever(None, [titled, untitled], k=2)

    result = retriever.retrieve("熊猫基地")

    assert result[0].metadata["chunk_id"] == "titled"


def test_synonym_expansion_recalls_document_using_different_wording():
    hotel_doc = Document(page_content="青羊区住宿片区靠近宽窄巷子。", metadata={"chunk_id": "hotel"})
    unrelated = Document(page_content="成都天气常年温和湿润。", metadata={"chunk_id": "weather"})
    retriever = HybridRetriever(None, [hotel_doc, unrelated], k=2)

    result = retriever.retrieve("酒店")

    assert result[0].metadata["chunk_id"] == "hotel"


def test_bigram_match_boosts_exact_phrase_over_scattered_terms():
    exact_phrase = Document(page_content="住宿环境干净整洁，适合家庭入住。", metadata={"chunk_id": "exact"})
    scattered = Document(page_content="住宿选择较多，环境保护也做得不错。", metadata={"chunk_id": "scattered"})
    retriever = HybridRetriever(None, [exact_phrase, scattered], k=2)

    result = retriever.retrieve("住宿环境")

    assert result[0].metadata["chunk_id"] == "exact"
