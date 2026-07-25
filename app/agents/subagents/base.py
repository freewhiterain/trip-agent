"""Shared runner for evidence-grounded domain subagents."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from pydantic import BaseModel, Field

from app.agents.subagents.tool_policy import ToolPolicy
from app.agents.subagents.tools import ProviderError, normalize_tool_result
from app.research.deep_search import DeepSearchRequest, run_deep_search
from app.schemas.planning import Evidence, ResearchTask, TaskType, TravelRequirement
from app.schemas.research import Claim, EvidenceBoundCandidate, ResearchReport, SubagentResponse


ToolBuilder = Callable[[TaskType], Awaitable[Iterable[Any]] | Iterable[Any]]
DeepSearchRunner = Callable[..., Awaitable[ResearchReport]]


class SubagentAnalysis(BaseModel):
    """Validated model output before evidence grounding."""

    summary: str = ""
    claims: list[Claim] = Field(default_factory=list)
    candidates: list[EvidenceBoundCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DomainSubagent:
    """Base domain runner with provider fallback and evidence grounding."""

    worker: TaskType
    provider_order: tuple[str, ...]
    allow_deep_search: bool

    def __init__(
        self,
        *,
        worker: TaskType,
        provider_order: Iterable[str],
        tools: Iterable[Any] | None = None,
        tool_builder: ToolBuilder | None = None,
        deep_search: DeepSearchRunner | None = None,
        llm: Any | None = None,
        **providers: Any,
    ) -> None:
        self.worker = worker
        self.provider_order = tuple(provider_order)
        self.policy = ToolPolicy.for_worker(worker)
        self.allow_deep_search = self.policy.allow_deep_research
        self.llm = llm
        self._tools = list(tools or [])
        self._tool_builder = tool_builder
        self._tools_loaded = tools is not None
        self._deep_search = deep_search or run_deep_search
        self._providers: dict[str, Any] = {
            self._canonical_provider(name): provider
            for name, provider in providers.items()
            if provider is not None
        }

    async def run(
        self,
        task: ResearchTask,
        requirement: TravelRequirement,
    ) -> SubagentResponse:
        """Run provider fallback and return only structured, grounded facts."""

        if task.task_type != self.worker:
            return self._failure_response(
                task,
                f"Task type {task.task_type} does not match subagent {self.worker}.",
            )

        warnings: list[str] = []
        evidence: list[Evidence] = []
        research_report: ResearchReport | None = None

        for provider in self.provider_order:
            provider_evidence, provider_warnings = await self._invoke_provider(
                provider,
                task,
                requirement,
            )
            warnings.extend(provider_warnings)
            if provider_evidence:
                evidence = provider_evidence
                break

        if not evidence and self.allow_deep_search:
            research_report = await self._run_deep_search(task, requirement)
            if research_report is not None:
                warnings.extend(research_report.warnings)
                evidence = self._normalize_evidence(
                    research_report.evidence,
                    "deep_research",
                )

        if not evidence:
            return SubagentResponse(
                task_id=task.id,
                worker=self.worker,
                status="unavailable",
                summary=f"No evidence is available for {self.worker}.",
                research_report=research_report,
                warnings=list(dict.fromkeys(warnings or [f"No provider returned evidence for {self.worker}."])),
            )

        analysis = await self._analyze(task, requirement, evidence)
        grounded, grounding_warnings = self._ground_analysis(analysis, evidence)
        warnings.extend(grounding_warnings)
        warnings.extend(grounded.warnings)

        status = "completed" if grounded.candidates or grounded.claims else "partial"
        if research_report is not None and research_report.status in {"partial", "unavailable", "failed"}:
            status = "partial" if evidence else "unavailable"

        return SubagentResponse(
            task_id=task.id,
            worker=self.worker,
            status=status,
            summary=grounded.summary or self._fallback_summary(evidence),
            claims=grounded.claims,
            candidates=grounded.candidates,
            evidence=evidence,
            research_report=research_report,
            warnings=list(dict.fromkeys(warnings)),
        )

    def build_query(self, task: ResearchTask, requirement: TravelRequirement) -> str:
        """Domain-specific query string for provider calls."""

        return task.query

    def build_prompt(
        self,
        task: ResearchTask,
        requirement: TravelRequirement,
        evidence: list[Evidence],
    ) -> list[dict[str, str]]:
        """Prompt for optional structured model analysis."""

        digest = "\n\n".join(
            f"[{item.id}] source={item.source}\n{item.content}" for item in evidence
        )
        return [
            {
                "role": "system",
                "content": (
                    f"You are the {self.worker} travel research subagent. "
                    "Return concise structured output only. Every claim and candidate "
                    "must cite one or more provided evidence IDs. Do not invent prices, "
                    "schedules, opening status, availability, inventory, weather, or other facts."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Requirement: {requirement.model_dump_json()}\n"
                    f"Task: {task.query}\n"
                    f"Evidence:\n{digest}"
                ),
            },
        ]

    async def _analyze(
        self,
        task: ResearchTask,
        requirement: TravelRequirement,
        evidence: list[Evidence],
    ) -> SubagentAnalysis:
        if self.llm is None:
            return self._deterministic_analysis(evidence)
        try:
            structured = self.llm.with_structured_output(SubagentAnalysis)
            response = await structured.ainvoke(self.build_prompt(task, requirement, evidence))
            return SubagentAnalysis.model_validate(response)
        except Exception as exc:
            fallback = self._deterministic_analysis(evidence)
            return fallback.model_copy(
                update={
                    "warnings": [
                        *fallback.warnings,
                        f"Structured {self.worker} analysis failed; used evidence fallback: {type(exc).__name__}.",
                    ]
                }
            )

    def _deterministic_analysis(self, evidence: list[Evidence]) -> SubagentAnalysis:
        candidates = [
            EvidenceBoundCandidate(
                id=item.id,
                name=self._evidence_title(item),
                category=self.worker,
                description=item.content[:240],
                attributes={"source": item.source},
                evidence_ids=[item.id] if item.id else [],
            )
            for item in evidence
        ]
        claims = [
            Claim(text=item.content[:240], evidence_ids=[item.id])
            for item in evidence
            if item.id
        ]
        return SubagentAnalysis(
            summary=self._fallback_summary(evidence),
            claims=claims,
            candidates=candidates,
            warnings=["This result is an evidence summary, not a live recommendation."],
        )

    def _ground_analysis(
        self,
        analysis: SubagentAnalysis,
        evidence: list[Evidence],
    ) -> tuple[SubagentAnalysis, list[str]]:
        evidence_ids = {item.id for item in evidence if item.id}
        warnings: list[str] = []

        claims: list[Claim] = []
        for claim in analysis.claims:
            claim_ids = set(claim.evidence_ids)
            if not claim_ids or not claim_ids.issubset(evidence_ids):
                warnings.append("Dropped an unbound claim from subagent output.")
                continue
            claims.append(claim)

        candidates: list[EvidenceBoundCandidate] = []
        for candidate in analysis.candidates:
            candidate_ids = set(candidate.evidence_ids)
            if not candidate.name.strip() or not candidate_ids or not candidate_ids.issubset(evidence_ids):
                warnings.append(f"Dropped an unbound {self.worker} candidate from subagent output.")
                continue
            candidates.append(candidate)

        return (
            analysis.model_copy(update={"claims": claims, "candidates": candidates}),
            warnings,
        )

    async def _invoke_provider(
        self,
        provider: str,
        task: ResearchTask,
        requirement: TravelRequirement,
    ) -> tuple[list[Evidence], list[str]]:
        tool = await self._tool_for(provider)
        if tool is None:
            return [], [f"{provider} is unavailable for {self.worker}."]

        payload = self._provider_payload(provider, task, requirement)
        try:
            raw_result = await self._call_tool(tool, payload)
        except Exception as exc:
            raw_result = exc

        normalized = self._normalized_provider_result(provider, raw_result)
        evidence = [item for item in normalized if isinstance(item, Evidence)]
        errors = [item for item in normalized if isinstance(item, ProviderError)]
        warnings = [f"{error.provider}: {error.code}" for error in errors]
        return self._normalize_evidence(evidence, provider), warnings

    async def _run_deep_search(
        self,
        task: ResearchTask,
        requirement: TravelRequirement,
    ) -> ResearchReport | None:
        if not self.allow_deep_search:
            return None

        search_provider = await self._tool_for("search_mcp") or await self._tool_for("deep_research")
        if search_provider is None:
            return ResearchReport(
                status="unavailable",
                warnings=["Deep Search skipped because no search provider is available."],
            )

        async def search(query: str, limit: int) -> list[Evidence]:
            payload = self._provider_payload("search_mcp", task, requirement)
            payload["query"] = query
            payload["limit"] = limit
            raw_result = await self._call_tool(search_provider, payload)
            normalized = self._normalized_provider_result("search_mcp", raw_result)
            return self._normalize_evidence(
                [item for item in normalized if isinstance(item, Evidence)],
                "deep_research",
            )

        request = DeepSearchRequest.from_task(
            task,
            tool_policy=self.policy,
            max_rounds=2,
            max_tool_calls=3,
            timeout_seconds=10.0,
            results_per_query=3,
        )
        return await self._deep_search(request, search=search)

    async def _tool_for(self, provider: str) -> Any | None:
        provider = self._canonical_provider(provider)
        if provider in self._providers:
            return self._providers[provider]

        await self._ensure_built_tools()
        for tool in self._tools:
            if self._tool_matches_provider(tool, provider):
                self._providers[provider] = tool
                return tool
        return None

    async def _ensure_built_tools(self) -> None:
        if self._tools_loaded or self._tool_builder is None:
            return
        tools = self._tool_builder(self.worker)
        if inspect.isawaitable(tools):
            tools = await tools
        self._tools = list(tools or [])
        self._tools_loaded = True

    @staticmethod
    async def _call_tool(tool: Any, payload: Mapping[str, Any]) -> Any:
        for method_name in ("ainvoke", "arun"):
            method = getattr(tool, method_name, None)
            if method is not None:
                result = method(payload)
                return await result if inspect.isawaitable(result) else result
        method = getattr(tool, "invoke", None)
        if method is not None:
            result = method(payload)
            return await result if inspect.isawaitable(result) else result
        if callable(tool):
            result = tool(payload)
            return await result if inspect.isawaitable(result) else result
        raise TypeError(f"Provider tool {tool!r} is not callable")

    def _provider_payload(
        self,
        provider: str,
        task: ResearchTask,
        requirement: TravelRequirement,
    ) -> dict[str, Any]:
        query = self.build_query(task, requirement)
        return {
            "provider": provider,
            "worker": self.worker,
            "task_id": task.id,
            "query": query,
            "destination": requirement.destination,
            "city": requirement.destination,
            "origin": requirement.origin,
            "departure_date": requirement.departure_date.isoformat(),
            "days": requirement.days,
            "category": self.worker,
            "forecast": self.worker == "weather",
        }

    def _normalized_provider_result(
        self,
        provider: str,
        raw_result: Any,
    ) -> list[Evidence | ProviderError]:
        if isinstance(raw_result, list) and all(
            isinstance(item, (Evidence, ProviderError)) for item in raw_result
        ):
            return raw_result
        return normalize_tool_result(provider, raw_result)

    @staticmethod
    def _canonical_provider(name: str) -> str:
        aliases = {
            "rag": "local_rag",
            "search": "search_mcp",
            "weather": "weather_mcp",
            "transport": "transport_mcp",
            "hotel": "hotel_mcp",
            "weather_fallback": "weather_fallback_api",
        }
        return aliases.get(name, name)

    @staticmethod
    def _tool_matches_provider(tool: Any, provider: str) -> bool:
        if getattr(tool, "provider", None) == provider:
            return True
        name = getattr(tool, "name", "")
        tool_name_to_provider = {
            "local_rag": "local_rag",
            "weather_fallback_api": "weather_fallback_api",
            "deep_research": "deep_research",
            "get_weather": "weather_mcp",
            "get_transport": "transport_mcp",
            "search_hotels": "hotel_mcp",
            "get_hotel_availability": "hotel_mcp",
            "web_search": "search_mcp",
        }
        return tool_name_to_provider.get(name) == provider

    def _normalize_evidence(
        self,
        evidence: Iterable[Evidence],
        provider: str,
    ) -> list[Evidence]:
        normalized: list[Evidence] = []
        seen: set[tuple[str, str, str]] = set()
        for index, item in enumerate(evidence, start=1):
            candidate = Evidence.model_validate(item)
            if not candidate.id:
                candidate = candidate.model_copy(update={"id": f"{self.worker}-{provider}-{index}"})
            key = (candidate.id or "", candidate.source_url or "", candidate.content)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(candidate)
        return normalized

    def _fallback_summary(self, evidence: list[Evidence]) -> str:
        return f"Found {len(evidence)} evidence item(s) for {self.worker}."

    @staticmethod
    def _evidence_title(item: Evidence) -> str:
        for line in item.content.splitlines():
            title = line.strip().lstrip("# ").strip()
            if line.lstrip().startswith("#") and title:
                return title[:80]
        return item.content.strip().splitlines()[0][:80]

    def _failure_response(self, task: ResearchTask, warning: str) -> SubagentResponse:
        return SubagentResponse(
            task_id=task.id,
            worker=task.task_type,
            status="failed",
            summary=f"{self.worker} subagent failed.",
            warnings=[warning],
        )


__all__ = ["DomainSubagent", "SubagentAnalysis"]
