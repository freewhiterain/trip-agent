"""Evidence 转换、时效检查和冲突标记。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from langchain_core.documents import Document

from app.schemas.planning import Evidence
from app.schemas.research import ResearchConflict


DEFAULT_TTLS = {
    "weather": timedelta(hours=1),
    "transport": timedelta(minutes=15),
    "hotel": timedelta(minutes=15),
    "price": timedelta(minutes=15),
    "opening_hours": timedelta(hours=6),
    "static": timedelta(days=180),
}

# 文档 category 到时效类别的映射。缺少这层推导时所有本地证据都会落到
# static（180 天），天气和交通这类易变事实会被当作半年有效，
# require_fresh_evidence 对它们形同虚设。
CATEGORY_EVIDENCE_TYPES = {
    "weather": "weather",
    "transport": "transport",
    "hotel": "hotel",
    "accommodation": "hotel",
}


def resolve_evidence_type(metadata: dict) -> str:
    """优先取显式 evidence_type，否则按文档类别推导时效类别。"""
    explicit = str(metadata.get("evidence_type", "")).strip()
    if explicit:
        return explicit
    category = str(metadata.get("category", "")).strip().casefold()
    return CATEGORY_EVIDENCE_TYPES.get(category, "static")


def evidence_from_document(
    document: Document,
    *,
    confidence: float = 0.7,
    now: datetime | None = None,
) -> Evidence:
    now = now or datetime.now(timezone.utc)
    category = resolve_evidence_type(document.metadata)
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
        metadata={**document.metadata, "evidence_type": category},
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
    """根据相同 fact_key 的不同 fact_value 标记来源冲突（人类可读文案）。"""
    return [
        f"事实 {conflict.fact_key} 存在冲突值：{', '.join(conflict.values)}"
        for conflict in detect_fact_conflicts(items)
    ]


def detect_fact_conflicts(items: list[Evidence]) -> list[ResearchConflict]:
    """同一 fact_key 出现多个 fact_value 时产出结构化冲突。

    这是全项目唯一的冲突检测实现：deep_search 的循环、治理层的复核和
    RAG 的文案提示此前各有一份逻辑相同的副本，任何一处改判定口径都会
    让另外两处静默地与它不一致。
    """
    by_key: dict[str, dict[str, list[str]]] = {}
    for item in items:
        fact_key = item.metadata.get("fact_key")
        fact_value = item.metadata.get("fact_value")
        if fact_key is None or fact_value is None:
            continue
        evidence_id = item.id or item.source_url or item.source
        by_key.setdefault(str(fact_key), {}).setdefault(str(fact_value), []).append(str(evidence_id))

    return [
        ResearchConflict(
            fact_key=fact_key,
            values=sorted(groups),
            evidence_ids=sorted({evidence_id for group in groups.values() for evidence_id in group}),
            description=f"证据在 {fact_key} 上给出了互相冲突的值。",
        )
        for fact_key, groups in by_key.items()
        if len(groups) > 1
    ]


def merge_fact_conflicts(
    declared: list[ResearchConflict],
    items: list[Evidence],
) -> list[ResearchConflict]:
    """把已声明的冲突与证据元数据推导出的冲突按 fact_key 合并，已声明者优先。"""
    merged = list(declared)
    seen = {conflict.fact_key for conflict in merged}
    for conflict in detect_fact_conflicts(items):
        if conflict.fact_key not in seen:
            merged.append(conflict)
            seen.add(conflict.fact_key)
    return merged
