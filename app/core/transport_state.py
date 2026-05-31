"""
交通规划专用状态扩展
"""
from typing import TypedDict, Annotated, Optional, Literal
from typing_extensions import NotRequired
from operator import add

TransportType = Literal["flight", "train", "driving"]


class FlightOption(TypedDict):
    flight_number: str
    airline: str
    departure_airport: str
    arrival_airport: str
    departure_time: str
    arrival_time: str
    duration: str
    price: float
    cabin_class: str
    available_seats: int


class TrainOption(TypedDict):
    train_number: str
    departure_station: str
    arrival_station: str
    departure_time: str
    arrival_time: str
    duration: str
    seat_types: list[str]
    prices: dict[str, float]
    available: bool


class DrivingRoute(TypedDict):
    route_name: str
    distance: str
    duration: str
    toll_fee: float
    fuel_cost: float
    steps: list[str]
    waypoints: list[str]


class TransportState(TypedDict):
    selected_transport: NotRequired[TransportType]
    origin_city: NotRequired[str]
    destination_city: NotRequired[str]
    departure_date: NotRequired[str]
    passenger_count: NotRequired[int]
    flight_options: Annotated[list[FlightOption], add]
    train_options: Annotated[list[TrainOption], add]
    driving_routes: Annotated[list[DrivingRoute], add]
    selected_flight: NotRequired[FlightOption]
    selected_train: NotRequired[TrainOption]
    selected_route: NotRequired[DrivingRoute]
