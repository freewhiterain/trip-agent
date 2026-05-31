"""
长期记忆数据模型
"""
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime


class UserProfile(BaseModel):
    travel_styles: list[str] = Field(default_factory=list)
    dietary_restrictions: list[str] = Field(default_factory=list)
    food_preferences: list[str] = Field(default_factory=list)
    updated_at: Optional[str] = Field(default=None)


class TravelRecord(BaseModel):
    destination: str = Field(...)
    start_date: str = Field(...)
    end_date: str = Field(...)
    visited_attractions: list[str] = Field(default_factory=list)


class AccommodationPreference(BaseModel):
    preferred_types: list[str] = Field(default_factory=list)
    avg_budget_per_night: Optional[float] = Field(default=None)


class TravelHistory(BaseModel):
    completed_trips: list[TravelRecord] = Field(default_factory=list)
    visited_attractions: list[str] = Field(default_factory=list)
    accommodation_preference: AccommodationPreference = Field(default_factory=AccommodationPreference)
    updated_at: Optional[str] = Field(default=None)


class UserMemory(BaseModel):
    user_id: str = Field(...)
    profile: UserProfile = Field(default_factory=UserProfile)
    history: TravelHistory = Field(default_factory=TravelHistory)
