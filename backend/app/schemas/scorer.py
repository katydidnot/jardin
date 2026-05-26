"""
app/schemas/scorer.py
======================
Pydantic schema for the structured output produced by the per-species
AI scoring call in services/species_scorer.py.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class SpeciesScores(BaseModel):
    """Structured ecological scores produced by Claude for one plant species."""

    pollinator_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Value to bees, butterflies, and other pollinators (0=none, 1=exceptional).",
    )
    insect_host_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Number of insect species that use this plant as a host (0=none, 1=very many).",
    )
    soil_benefit_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Soil health benefit from N-fixing, aeration, organic matter, etc. (0=none, 1=exceptional).",
    )
    environmental_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Carbon sequestration, water retention, biodiversity support (0=minimal, 1=exceptional).",
    )
    food_utility_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Usefulness of edible/medicinal parts (0=not useful, 1=highly valuable).",
    )
    food_utility_notes: str = Field(
        description=(
            "Brief description of edible parts, medicinal uses, or "
            "'Not commonly used for food or medicine' if applicable."
        ),
    )

    @field_validator(
        "pollinator_score",
        "insect_host_score",
        "soil_benefit_score",
        "environmental_score",
        "food_utility_score",
        mode="before",
    )
    @classmethod
    def clamp_score(cls, v: Any) -> float:
        """Clamp to [0.0, 1.0] to tolerate minor model rounding."""
        return max(0.0, min(1.0, float(v)))
