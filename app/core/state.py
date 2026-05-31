"""
TravelState 状态定义
"""
from typing import Annotated, Literal, Optional
from typing_extensions import TypedDict, NotRequired
from operator import add
from langchain.agents import AgentState


PlanningStep = Literal[
    "requirement_collection",
    "destination_recommendation",
    "transport_planning",
    "accommodation_planning",
    "food_planning",
    "itinerary_generation",
    "budget_summarization",
    "order_generation"
]

TravelStyle = Literal["relaxation", "culture", "adventure", "food"]

BudgetLevel = Literal["economy", "comfort", "luxury"]

TransportType = Literal["flight", "train", "driving"]

AccommodationType = Literal["star_hotel", "economy_hotel", "hostel", "youth_hostel"]

FoodType = Literal["specialty", "chain", "local"]


class UserRequirement(TypedDict):
    departure_city: str
    destination: Optional[str]
    departure_date: str
    travel_days: int
    adult_count: int
    children_count: int
    budget_min: Optional[float]
    budget_max: Optional[float]
    budget_level: BudgetLevel
    travel_styles: list[TravelStyle]
    special_needs: Optional[str]


class DestinationInfo(TypedDict):
    name: str
    description: str
    weather_info: Optional[str]
    attractions: list[str]
    estimated_cost: Optional[float]


class TransportInfo(TypedDict):
    transport_type: TransportType
    details: str
    departure_time: str
    arrival_time: str
    duration: str
    price: float


class AccommodationInfo(TypedDict):
    name: str
    type: AccommodationType
    location: str
    price_per_night: float
    rating: Optional[float]
    amenities: list[str]


class FoodInfo(TypedDict):
    type: FoodType
    recommendations: list[str]
    estimated_daily_cost: float


class ItineraryDay(TypedDict):
    day_number: int
    activities: list[str]
    meals: list[str]
    accommodation: str


class BudgetBreakdown(TypedDict):
    transport: float
    accommodation: float
    food: float
    attractions: float
    misc: float
    total: float


class TravelState(AgentState):
    """旅行规划系统主状态"""

    current_step: NotRequired[PlanningStep]
    user_requirement: NotRequired[UserRequirement]

    selected_destination: NotRequired[str]
    selected_transport: NotRequired[TransportType]
    selected_accommodation_types: NotRequired[list[AccommodationType]]
    selected_food_types: NotRequired[list[FoodType]]

    destination_options: NotRequired[list[DestinationInfo]]
    transport_options: NotRequired[list[TransportInfo]]
    accommodation_options: NotRequired[list[AccommodationInfo]]
    food_options: NotRequired[list[FoodInfo]]

    itinerary: NotRequired[list[ItineraryDay]]
    budget: NotRequired[BudgetBreakdown]
    report: NotRequired[str]
    order_id: NotRequired[str]

    approval_pending: NotRequired[bool]
    approval_reason: NotRequired[str]

    user_id: NotRequired[str]
    session_id: NotRequired[str]
    created_at: NotRequired[float]
    updated_at: NotRequired[float]


def create_initial_state(user_id: str, session_id: str) -> TravelState:
    """创建初始状态"""
    import time
    return TravelState(
        messages=[],
        current_step="requirement_collection",
        destination_options=[],
        transport_options=[],
        accommodation_options=[],
        food_options=[],
        approval_pending=False,
        user_id=user_id,
        session_id=session_id,
        created_at=time.time(),
        updated_at=time.time()
    )
