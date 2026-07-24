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
