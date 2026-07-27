## Task 3: Optional LLM-Assisted Relation Extraction

**Files:**
- Modify: `app/rag/graph_extraction.py`
- Test: `tests/test_graph_extraction.py`

**Interfaces:**
- Consumes: an object exposing `.with_structured_output(schema)` returning an
  object with async `.ainvoke(messages) -> BaseModel` (same shape as the `llm`
  parameter already used by `app/agents/workers/rag_analysis.py`).
- Produces: `async extract_relations_with_llm(document: Document, llm) ->
  list[ExtractedRelation]`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_graph_extraction.py
import pytest

from app.rag.graph_extraction import extract_relations_with_llm


class _FakeStructuredLlm:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.messages = None

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, messages):
        self.messages = messages
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.asyncio
async def test_extract_relations_with_llm_returns_empty_when_llm_is_none():
    assert await extract_relations_with_llm(attractions_doc(), None) == []


@pytest.mark.asyncio
async def test_extract_relations_with_llm_returns_empty_on_failure():
    llm = _FakeStructuredLlm(error=RuntimeError("model unavailable"))

    assert await extract_relations_with_llm(attractions_doc(), llm) == []


@pytest.mark.asyncio
async def test_extract_relations_with_llm_maps_structured_response():
    from app.rag.graph_extraction import _LLMExtraction, _LLMRelation

    llm = _FakeStructuredLlm(
        response=_LLMExtraction(
            relations=[_LLMRelation(from_name="宽窄巷子", relation_type="near", to_name="武侯祠")]
        )
    )

    relations = await extract_relations_with_llm(attractions_doc(), llm)

    assert len(relations) == 1
    assert relations[0].from_name == "宽窄巷子"
    assert relations[0].relation_type == "near"
    assert relations[0].to_name == "武侯祠"
    assert relations[0].confidence == 0.6
    assert relations[0].city == "成都"
    prompt = "\n".join(message["content"] for message in llm.messages)
    assert "不得编造" in prompt
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_extraction.py -k llm -q`

Expected: FAIL with `ImportError: cannot import name 'extract_relations_with_llm'`.

- [ ] **Step 3: Implement the LLM-assisted extractor**

```python
# append to app/rag/graph_extraction.py
from typing import Any, Literal

from pydantic import BaseModel, Field


class _LLMRelation(BaseModel):
    from_name: str
    relation_type: Literal["located_in", "near", "connects_to"]
    to_name: str


class _LLMExtraction(BaseModel):
    relations: list[_LLMRelation] = Field(default_factory=list)


async def extract_relations_with_llm(document: Document, llm: Any | None) -> list[ExtractedRelation]:
    city = str(document.metadata.get("city", "")).strip()
    category = str(document.metadata.get("category", "")).strip()
    source = str(document.metadata.get("source", ""))
    if llm is None or not city or not category:
        return []
    try:
        structured = llm.with_structured_output(_LLMExtraction)
        response = await structured.ainvoke(
            [
                {
                    "role": "system",
                    "content": (
                        "你是知识图谱抽取助手。只能使用给定文档内容识别实体之间的关系，"
                        "不得编造文档中不存在的实体或关系。relation_type 只能是 "
                        "located_in、near 或 connects_to。"
                    ),
                },
                {"role": "user", "content": document.page_content},
            ]
        )
        extraction = _LLMExtraction.model_validate(response)
    except Exception:
        return []
    return [
        ExtractedRelation(
            city=city, from_name=item.from_name.strip(), from_category=category,
            relation_type=item.relation_type, to_name=item.to_name.strip(),
            source_document=source, confidence=0.6,
        )
        for item in extraction.relations
        if item.from_name.strip() and item.to_name.strip()
    ]
```

- [ ] **Step 4: Run the full extraction test file**

Run: `.venv\Scripts\python.exe -m pytest tests/test_graph_extraction.py -q`

Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add app/rag/graph_extraction.py tests/test_graph_extraction.py
git commit -m "feat: add optional LLM-assisted relation extraction with deterministic fallback"
```

(Skip if the user has asked not to auto-commit.)

---

