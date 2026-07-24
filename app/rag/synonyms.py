"""旅行领域同义词表：用于查询构造阶段的召回扩展，不参与字段加权判断。"""

SYNONYM_GROUPS: list[set[str]] = [
    {"宾馆", "酒店", "住宿"},
    {"景点", "景区", "游览地"},
    {"美食", "小吃", "餐馆"},
    {"交通", "出行", "班次"},
    {"天气", "气候"},
]

_SYNONYM_LOOKUP: dict[str, set[str]] = {}
for _group in SYNONYM_GROUPS:
    for _term in _group:
        _SYNONYM_LOOKUP[_term] = _group - {_term}


def expand_synonyms(terms: list[str]) -> list[str]:
    """返回命中同义词表的词对应的近义词（不含原词），仅用于扩大召回。"""
    expanded: list[str] = []
    for term in terms:
        expanded.extend(sorted(_SYNONYM_LOOKUP.get(term, set())))
    return expanded
