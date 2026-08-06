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
