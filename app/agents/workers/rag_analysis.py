"""Evidence-bound analysis shared by local RAG Workers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.planning import (
    CandidateOption,
    Evidence,
    ResearchTask,
    TaskType,
    TravelRequirement,
    WorkerResult,
)


class WorkerAnalysis(BaseModel):
    """Structured Worker conclusion; it never contains hidden reasoning."""

    summary: str
    options: list[CandidateOption] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    used_mock_data: bool = False


def worker_result_from_analysis(
    task: ResearchTask,
    worker: TaskType,
    evidence: list[Evidence],
    analysis: WorkerAnalysis,
) -> WorkerResult:
    """Map an evidence-bound analysis to the public Worker contract."""
    status = "unavailable" if not evidence else "completed" if analysis.options else "partial"
    return WorkerResult(
        task_id=task.id,
        worker=worker,
        status=status,
        summary=analysis.summary,
        options=analysis.options,
        evidence=evidence,
        warnings=analysis.warnings,
        is_mock=True,
    )


def _evidence_title(item: Evidence) -> str:
    for line in item.content.splitlines():
        title = line.strip().lstrip("# ").strip()
        if line.lstrip().startswith("#") and title:
            return title
    return item.content.strip().splitlines()[0][:80]


def _deterministic_analysis(
    worker: TaskType,
    evidence: list[Evidence],
    warning: str | None = None,
) -> WorkerAnalysis:
    if not evidence:
        warnings = ["No evidence is available for this analysis."]
        if warning:
            warnings.insert(0, warning)
        return WorkerAnalysis(
            summary="No evidence is available for this analysis.",
            warnings=warnings,
            used_mock_data=False,
        )

    options = [
        CandidateOption(
            name=_evidence_title(item),
            category=worker,
            description=item.content[:240],
            attributes={"source": item.source},
        )
        for item in evidence
    ]
    warnings = ["This is an evidence summary, not a live recommendation."]
    if warning:
        warnings.insert(0, warning)
    return WorkerAnalysis(
        summary=f"根据检索到的 {worker} 证据整理了 {len(options)} 条候选信息。",
        options=options,
        warnings=warnings,
        used_mock_data=any(
            item.metadata.get("source_type") == "mock_markdown" for item in evidence
        ),
    )


def _ground_options(
    worker: TaskType,
    options: list[CandidateOption],
    evidence: list[Evidence],
) -> tuple[list[CandidateOption], list[str]]:
    grounded: list[CandidateOption] = []
    warnings: list[str] = []
    for option in options:
        option_name = option.name.strip()
        if not option_name:
            warnings.append(f"已丢弃缺少证据支持的 {worker} 候选：空名称")
            continue
        match = next(
            (
                item
                for item in evidence
                if (
                    option_name.casefold() in {
                        line.strip().lstrip("# ").strip().casefold()
                        for line in item.content.splitlines()
                        if line.lstrip().startswith("#")
                    }
                    or (
                        len(option_name) >= 4
                        and option_name.casefold() in item.content.casefold()
                    )
                )
            ),
            None,
        )
        if match is None:
            warnings.append(f"已丢弃缺少证据支持的 {worker} 候选：{option_name}")
            continue
        grounded.append(
            CandidateOption(
                name=option_name,
                category=worker,
                description=match.content[:240],
                attributes={"source": match.source},
            )
        )
    return grounded, warnings


def _prompt(
    worker: TaskType,
    task: ResearchTask,
    requirement: TravelRequirement,
    evidence: list[Evidence],
) -> list[dict[str, str]]:
    digest = "\n\n".join(
        f"来源：{item.source}\n内容：{item.content}" for item in evidence
    )
    return [
        {
            "role": "system",
            "content": (
                f"你是 {worker} 旅行研究 Worker。只能使用用户提供的证据。"
                "不得编造价格、班次、营业状态、库存或天气事实。"
                "Do not invent prices, schedules, operating status, inventory, or weather facts."
                "缺乏证据的字段必须留空并加入 warning。"
                "Do not expose hidden chain-of-thought。只返回简短摘要、候选项和警告。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"需求：{requirement.model_dump_json()}\n"
                f"任务：{task.query}\n"
                f"证据：\n{digest}"
            ),
        },
    ]


async def analyze_worker_evidence(
    worker: TaskType,
    task: ResearchTask,
    requirement: TravelRequirement,
    evidence: list[Evidence],
    *,
    llm: Any | None = None,
) -> WorkerAnalysis:
    """Analyze retrieved evidence, falling back without inventing facts."""

    if not evidence:
        return _deterministic_analysis(worker, evidence)
    if llm is None:
        return _deterministic_analysis(worker, evidence)

    try:
        structured = llm.with_structured_output(WorkerAnalysis)
        response = await structured.ainvoke(_prompt(worker, task, requirement, evidence))
        analysis = WorkerAnalysis.model_validate(response)
        options, grounding_warnings = _ground_options(worker, analysis.options, evidence)
        return analysis.model_copy(
            update={
                "summary": f"根据检索到的 {worker} 证据整理了 {len(options)} 条候选信息。",
                "options": options,
                "warnings": list(dict.fromkeys([*analysis.warnings, *grounding_warnings])),
                "used_mock_data": any(
                    item.metadata.get("source_type") == "mock_markdown" for item in evidence
                ),
            }
        )
    except Exception as exc:
        return _deterministic_analysis(
            worker,
            evidence,
            warning=f"结构化 Worker 分析失败，已降级为证据摘要：{type(exc).__name__}。",
        )
