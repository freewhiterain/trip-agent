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
