from pathlib import Path

from langchain_community.document_loaders.helpers import FileEncoding
from langchain_community.document_loaders import text as text_loader

from app.rag.document_loader import DocumentManager


def test_chengdu_mock_documents_have_worker_metadata():
    expected_categories = {
        "attractions": "attractions",
        "weather": "weather",
        "transport": "transport",
        "accommodation": "hotel",
        "food": "food",
    }
    documents = DocumentManager().load_all_documents()

    chengdu_documents = {
        Path(str(document.metadata["source"])).parent.name: document
        for document in documents
        if Path(str(document.metadata["source"])).name == "chengdu.md"
        and Path(str(document.metadata["source"])).parent.name in expected_categories
    }

    assert set(chengdu_documents) == set(expected_categories)
    for directory, category in expected_categories.items():
        document = chengdu_documents[directory]
        metadata = document.metadata
        assert "数据类型：模拟资料" in document.page_content
        assert "适用城市：成都" in document.page_content
        assert "最后更新：开发测试数据" in document.page_content
        assert metadata["city"] == "成都"
        assert metadata["source_type"] == "mock_markdown"
        assert metadata["category"] == category


def test_loader_falls_back_to_detected_encoding_for_unrelated_documents(tmp_path, monkeypatch):
    source_fixture = Path(__file__).parent.parent / "data" / "documents" / "food" / "chengdu.md"
    target_fixture = tmp_path / "food" / "chengdu.md"
    target_fixture.parent.mkdir(parents=True)
    target_fixture.write_bytes(source_fixture.read_bytes())
    (tmp_path / "food" / "legacy.md").write_bytes("café".encode("latin-1"))
    monkeypatch.setattr(
        text_loader,
        "detect_file_encodings",
        lambda path: [FileEncoding("latin-1", 1.0, None)],
    )

    documents = DocumentManager(base_dir=tmp_path).load_food_documents()

    assert {document.page_content for document in documents} == {
        source_fixture.read_text(encoding="utf-8"),
        "café",
    }
