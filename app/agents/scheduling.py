"""Deterministic itinerary scheduling and evidence-backed budget calculation."""

from __future__ import annotations

from collections.abc import Iterable

from app.schemas.planning import (
    BudgetSummary,
    CandidateOption,
    ItineraryDay,
    TimeSlot,
    TravelRequirement,
    WorkerResult,
)


def _by_worker(results: Iterable[WorkerResult], worker: str) -> WorkerResult | None:
    return next((result for result in results if result.worker == worker), None)


def _slot_from_option(period: str, option: CandidateOption) -> TimeSlot:
    attributes = option.attributes
    travel_minutes = attributes.get("travel_minutes")
    if not isinstance(travel_minutes, int) or travel_minutes < 0:
        travel_minutes = None
    location = attributes.get("location")
    opening_window = attributes.get("opening_hours")
    return TimeSlot(
        period=period,
        title=option.name,
        description=option.description,
        location=str(location) if location else None,
        travel_minutes=travel_minutes,
        opening_window=str(opening_window) if opening_window else None,
        estimated_cost=option.estimated_cost,
        evidence_indexes=[],
    )


def schedule_itinerary(
    requirement: TravelRequirement,
    results: list[WorkerResult],
) -> tuple[list[ItineraryDay], list[str]]:
    """Assign distinct grounded candidates to stable day/period slots."""

    attraction_result = _by_worker(results, "attractions")
    food_result = _by_worker(results, "food")
    attractions = list(attraction_result.options if attraction_result else [])
    foods = list(food_result.options if food_result else [])
    used_attractions: set[str] = set()
    warnings: list[str] = []
    itinerary: list[ItineraryDay] = []

    for offset in range(requirement.days):
        selected: list[CandidateOption] = []
        for _ in range(2):
            candidate = next(
                (item for item in attractions if item.name not in used_attractions),
                None,
            )
            if candidate is None:
                break
            selected.append(candidate)
            used_attractions.add(candidate.name)

        day_notes: list[str] = []
        if not selected:
            day_notes.append(f"No unused attraction candidate remains for day {offset + 1}.")
        morning = (
            _slot_from_option("morning", selected[0])
            if selected
            else TimeSlot(
                period="morning",
                title=f"{requirement.destination} flexible area time",
                description="No evidence-backed attraction remains for this period.",
            )
        )
        afternoon = (
            _slot_from_option("afternoon", selected[1])
            if len(selected) > 1
            else TimeSlot(
                period="afternoon",
                title="Flexible local time",
                description="No second evidence-backed attraction was available.",
            )
        )
        evening = (
            _slot_from_option("evening", foods[offset % len(foods)])
            if foods
            else TimeSlot(
                period="evening",
                title="Flexible dinner time",
                description="No evidence-backed food candidate was available.",
            )
        )
        itinerary.append(
            ItineraryDay(
                day=offset + 1,
                date=requirement.departure_date.fromordinal(
                    requirement.departure_date.toordinal() + offset
                ),
                slots=[morning, afternoon, evening],
                notes=day_notes,
            )
        )
    return itinerary, warnings


def _sum_cost(options: list[CandidateOption], *, multiplier: int = 1) -> float | None:
    priced = [option.estimated_cost for option in options if option.estimated_cost is not None]
    if not priced:
        return None
    return float(sum(priced) * multiplier)


def calculate_budget(
    requirement: TravelRequirement,
    results: list[WorkerResult],
) -> BudgetSummary:
    """Sum only evidence-backed candidate costs and expose missing categories."""

    attractions = list((_by_worker(results, "attractions") or WorkerResult(
        task_id="budget-attractions", worker="attractions", status="unavailable", summary=""
    )).options)
    food = list((_by_worker(results, "food") or WorkerResult(
        task_id="budget-food", worker="food", status="unavailable", summary=""
    )).options)
    hotel = list((_by_worker(results, "hotel") or WorkerResult(
        task_id="budget-hotel", worker="hotel", status="unavailable", summary=""
    )).options)
    transport = list((_by_worker(results, "transport") or WorkerResult(
        task_id="budget-transport", worker="transport", status="unavailable", summary=""
    )).options)

    accommodation_cost: float | None = None
    if hotel:
        per_night = [item for item in hotel if item.attributes.get("pricing_unit") == "per_night"]
        accommodation_cost = _sum_cost(per_night, multiplier=requirement.days) if per_night else _sum_cost(hotel)

    categories = {
        "transport": _sum_cost(transport),
        "accommodation": accommodation_cost,
        "food": _sum_cost(food, multiplier=requirement.days),
        "attractions": _sum_cost(attractions),
        "misc": None,
    }
    known_costs = [value for value in categories.values() if value is not None]
    notes = [
        "Budget includes only candidate prices supported by research evidence.",
        "Missing categories are excluded from the total estimate.",
    ]
    if requirement.budget is not None:
        notes.insert(0, f"User budget limit: {requirement.budget:.2f} CNY.")
    else:
        notes.insert(0, "No explicit user budget was provided.")
    return BudgetSummary(
        total_estimate=float(sum(known_costs)) if known_costs else None,
        categories=categories,
        notes=notes,
    )


__all__ = ["calculate_budget", "schedule_itinerary"]
