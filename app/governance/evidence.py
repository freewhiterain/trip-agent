"""Deterministic review of evidence-bound subagent research results."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.planning import Evidence
from app.schemas.research import Claim, EvidenceBoundCandidate, ResearchConflict, SubagentResponse


SOURCE_RANKS: dict[str, int] = {
    "official": 0,
    "weather": 1,
    "weather_mcp": 1,
    "transport": 2,
    "transport_mcp": 2,
    "hotel": 3,
    "hotel_mcp": 3,
    "local": 4,
    "local_rag": 4,
    "rag": 4,
    "search": 5,
    "search_mcp": 5,
    "web": 5,
    "fallback": 6,
}


class ReviewedResearch(BaseModel):
    """Governed research payload safe for downstream planning use."""

    claims: list[Claim] = Field(default_factory=list)
    candidates: list[EvidenceBoundCandidate] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    conflicts: list[ResearchConflict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EvidenceGovernanceService:
    """Validate, deduplicate, and filter evidence-bound subagent output."""

    def __init__(self, *, now: datetime | None = None):
        self._now = now

    def review(self, results: Iterable[SubagentResponse | Mapping[str, Any]]) -> ReviewedResearch:
        warnings: list[str] = []
        responses = [
            result if isinstance(result, SubagentResponse) else SubagentResponse.model_validate(result)
            for result in results
        ]
        usable_evidence, id_remap = self._review_evidence(responses, warnings)
        usable_ids = {item.id for item in usable_evidence if item.id}

        claims: list[Claim] = []
        seen_claims: set[tuple[str, tuple[str, ...]]] = set()
        candidates: list[EvidenceBoundCandidate] = []
        seen_candidates: set[tuple[str, str, tuple[str, ...]]] = set()
        conflicts: list[ResearchConflict] = []

        for response in responses:
            warnings.extend(response.warnings)
            claim_sources = list(response.claims)
            candidate_sources = list(response.candidates)
            if response.research_report is not None:
                warnings.extend(response.research_report.warnings)
                claim_sources.extend(response.research_report.claims)
                conflicts.extend(response.research_report.conflicts)

            for claim in claim_sources:
                remapped_ids = self._remap_ids(claim.evidence_ids, id_remap, usable_ids)
                if not remapped_ids:
                    warnings.append(
                        f"Dropped unbound claim for task {response.task_id}: {claim.text[:80]}"
                    )
                    continue
                key = (claim.text, tuple(remapped_ids))
                if key in seen_claims:
                    continue
                seen_claims.add(key)
                claims.append(claim.model_copy(update={"evidence_ids": remapped_ids}))

            for candidate in candidate_sources:
                remapped_ids = self._remap_ids(candidate.evidence_ids, id_remap, usable_ids)
                if not remapped_ids:
                    warnings.append(
                        f"Dropped unbound candidate for task {response.task_id}: {candidate.name[:80]}"
                    )
                    continue
                key = (candidate.name, candidate.category, tuple(remapped_ids))
                if key in seen_candidates:
                    continue
                seen_candidates.add(key)
                candidates.append(candidate.model_copy(update={"evidence_ids": remapped_ids}))

        return ReviewedResearch(
            claims=claims,
            candidates=candidates,
            evidence=usable_evidence,
            conflicts=conflicts,
            warnings=list(dict.fromkeys(warnings)),
        )

    def _review_evidence(
        self,
        responses: list[SubagentResponse],
        warnings: list[str],
    ) -> tuple[list[Evidence], dict[str, str | None]]:
        id_remap: dict[str, str | None] = {}
        evidence_by_key: dict[str, Evidence] = {}
        order_by_key: dict[str, int] = {}
        key_by_evidence_id: dict[str, str] = {}
        evidence_ids_by_key: dict[str, set[str]] = {}
        now = self._as_aware_utc(self._now or datetime.now(timezone.utc))

        for response in responses:
            evidence_items = list(response.evidence)
            if response.research_report is not None:
                evidence_items.extend(response.research_report.evidence)

            for evidence in evidence_items:
                if not evidence.id:
                    warnings.append(f"Dropped evidence without id for task {response.task_id}.")
                    continue
                key = self._dedupe_key(evidence)
                valid_until = (
                    self._as_aware_utc(evidence.valid_until)
                    if evidence.valid_until is not None
                    else None
                )
                if valid_until is not None:
                    evidence = evidence.model_copy(update={"valid_until": valid_until})
                if valid_until is not None and valid_until < now:
                    id_remap[evidence.id] = None
                    warnings.append(f"Dropped expired evidence {evidence.id} for task {response.task_id}.")
                    continue
                if evidence.id in key_by_evidence_id and key_by_evidence_id[evidence.id] != key:
                    id_remap[evidence.id] = None
                    warnings.append(f"Dropped conflicting duplicate evidence id {evidence.id}.")
                    continue

                key_by_evidence_id[evidence.id] = key
                existing = evidence_by_key.get(key)
                if existing is None:
                    evidence_by_key[key] = evidence
                    order_by_key[key] = len(order_by_key)
                    evidence_ids_by_key[key] = {evidence.id}
                    id_remap[evidence.id] = evidence.id
                    continue

                evidence_ids_by_key.setdefault(key, set()).add(evidence.id)
                preferred = self._preferred_evidence(existing, evidence)
                evidence_by_key[key] = preferred
                kept_id = preferred.id
                for duplicate_id in evidence_ids_by_key[key]:
                    id_remap[duplicate_id] = kept_id
                warnings.append(
                    f"Deduplicated evidence {evidence.id} into {kept_id} for task {response.task_id}."
                )

        ordered_keys = sorted(order_by_key, key=order_by_key.__getitem__)
        return [evidence_by_key[key] for key in ordered_keys], id_remap

    @staticmethod
    def _dedupe_key(evidence: Evidence) -> str:
        if evidence.source_url:
            return f"url:{evidence.source_url.strip().lower()}"
        normalized_content = " ".join(evidence.content.split()).lower()
        return f"content:{normalized_content}"

    @staticmethod
    def _source_rank(evidence: Evidence) -> tuple[int, float]:
        return (SOURCE_RANKS.get(evidence.source.lower(), 99), -evidence.confidence)

    @staticmethod
    def _as_aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _preferred_evidence(self, current: Evidence, candidate: Evidence) -> Evidence:
        if self._source_rank(candidate) < self._source_rank(current):
            return candidate
        return current

    @staticmethod
    def _remap_ids(
        evidence_ids: list[str],
        id_remap: dict[str, str | None],
        usable_ids: set[str | None],
    ) -> list[str]:
        remapped: list[str] = []
        for evidence_id in evidence_ids:
            mapped = id_remap.get(evidence_id)
            if mapped is None or mapped not in usable_ids:
                continue
            remapped.append(mapped)
        return list(dict.fromkeys(remapped))


__all__ = ["EvidenceGovernanceService", "ReviewedResearch"]
