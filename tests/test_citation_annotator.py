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
