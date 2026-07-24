import pytest

from app.governance.approvals import ApprovalService, InMemoryApprovalRepository
from app.governance.itineraries import InMemoryItineraryRepository, ItineraryGovernanceService
from app.memory.trip_history import InMemoryTripHistoryRepository


def _content():
    return {
        "requirement": {"destination": "成都", "departure_date": "2026-08-01", "days": 3},
        "itinerary": [
            {
                "day": 1,
                "date": "2026-08-01",
                "slots": [{"period": "morning", "title": "熊猫基地", "description": ""}],
            }
        ],
    }


@pytest.mark.asyncio
async def test_confirmed_itinerary_save_appends_trip_history():
    approvals = ApprovalService(InMemoryApprovalRepository())
    itineraries = InMemoryItineraryRepository()
    trip_history = InMemoryTripHistoryRepository()
    service = ItineraryGovernanceService(approvals, itineraries, trip_history)
    request = await service.request_save("t", "u", "c", "成都行程", _content())
    await approvals.decide(request.id, "u", "approve")

    await service.apply(request.id, "u")

    history = await trip_history.list("u")
    assert len(history) == 1
    assert history[0].destination == "成都"
    assert history[0].visited_attractions == ["熊猫基地"]


@pytest.mark.asyncio
async def test_itinerary_save_succeeds_even_when_trip_history_repository_fails():
    class FailingTripHistoryRepository:
        async def append(self, record):
            raise RuntimeError("db unavailable")

        async def list(self, user_id):
            return []

    approvals = ApprovalService(InMemoryApprovalRepository())
    itineraries = InMemoryItineraryRepository()
    service = ItineraryGovernanceService(approvals, itineraries, FailingTripHistoryRepository())
    request = await service.request_save("t", "u", "c", "成都行程", _content())
    await approvals.decide(request.id, "u", "approve")

    saved = await service.apply(request.id, "u")

    assert saved["version"] == 1


@pytest.mark.asyncio
async def test_itinerary_save_works_without_trip_history_repository_configured():
    approvals = ApprovalService(InMemoryApprovalRepository())
    itineraries = InMemoryItineraryRepository()
    service = ItineraryGovernanceService(approvals, itineraries)
    request = await service.request_save("t", "u", "c", "成都行程", _content())
    await approvals.decide(request.id, "u", "approve")

    saved = await service.apply(request.id, "u")

    assert saved["version"] == 1
