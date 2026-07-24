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
