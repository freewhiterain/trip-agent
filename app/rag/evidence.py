"""Evidence 转换、时效检查和冲突标记。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from langchain_core.documents import Document

from app.schemas.planning import Evidence


DEFAULT_TTLS = {
    "weather": timedelta(hours=1),
    "transport": timedelta(minutes=15),
    "hotel": timedelta(minutes=15),
    "price": timedelta(minutes=15),
    "opening_hours": timedelta(hours=6),
    "static": timedelta(days=180),
}


def evidence_from_document(
    document: Document,
    *,
    confidence: float = 0.7,
    now: datetime | None = None,
) -> Evidence:
    now = now or datetime.now(timezone.utc)
    category = str(document.metadata.get("evidence_type", "static"))
    ttl = DEFAULT_TTLS.get(category, DEFAULT_TTLS["static"])
    source = str(document.metadata.get("source") or document.metadata.get("path") or "本地知识库")
    return Evidence(
        content=document.page_content,
        source=source,
        source_url=document.metadata.get("source_url"),
        retrieved_at=now,
        valid_from=now,
        valid_until=now + ttl,
        confidence=confidence,
        metadata=dict(document.metadata),
    )


def is_evidence_fresh(evidence: Evidence, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if evidence.valid_from and now < evidence.valid_from:
        return False
    if evidence.valid_until and now > evidence.valid_until:
        return False
    return True


def require_fresh_evidence(items: list[Evidence], now: datetime | None = None) -> list[Evidence]:
    return [item for item in items if is_evidence_fresh(item, now)]


def find_conflicts(items: list[Evidence]) -> list[str]:
    """根据相同 fact_key 的不同 fact_value 标记来源冲突。"""
    values: dict[str, dict[str, set[str]]] = {}
    for item in items:
        key = item.metadata.get("fact_key")
        value = item.metadata.get("fact_value")
        if key is None or value is None:
            continue
        values.setdefault(str(key), {}).setdefault(str(value), set()).add(item.source)
    return [
        f"事实 {key} 存在冲突值：{', '.join(sorted(group))}"
        for key, group in values.items()
        if len(group) > 1
    ]
