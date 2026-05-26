"""
app/schemas/recommendations.py
================================
Pydantic request/response models for the recommendations and species endpoints.

These are the API-layer data shapes — distinct from the SQLAlchemy ORM models
in app/models/ which define the database tables.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class RecommendationRequest(BaseModel):
    """POST /api/recommendations — garden planting request body."""

    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    country_code: str = Field(..., min_length=2, max_length=3)
    region: str | None = None
    priority_pollinators: float = Field(0.5, ge=0.0, le=1.0)
    priority_insects: float = Field(0.5, ge=0.0, le=1.0)
    priority_soil: float = Field(0.5, ge=0.0, le=1.0)
    priority_environment: float = Field(0.5, ge=0.0, le=1.0)
    priority_food_utility: float = Field(0.5, ge=0.0, le=1.0)
    priority_size: float = Field(0.5, ge=0.0, le=1.0)
    description: str | None = Field(None, max_length=2000)

    @field_validator("country_code")
    @classmethod
    def upper_country_code(cls, v: str) -> str:
        return v.upper()


class RecommendationCreated(BaseModel):
    """POST /api/recommendations — 202 Accepted response."""

    request_id: str
    status: str = "processing"
    stream_url: str


class SpeciesOut(BaseModel):
    """
    Full species detail returned by GET /api/species/{id} and embedded inside
    recommendation payloads.

    ``bloom_months`` is a list of integers 1–12 representing months with active
    bloom; ``None`` / absent means unknown.
    """

    id: str
    scientific_name: str
    common_names: list[str]
    family: str | None
    description: str | None
    food_utility_notes: str | None
    pollinator_score: float | None
    insect_host_score: float | None
    soil_benefit_score: float | None
    environmental_score: float | None
    food_utility_score: float | None
    bloom_months: list[int] | None = None

    model_config = {"from_attributes": True}
