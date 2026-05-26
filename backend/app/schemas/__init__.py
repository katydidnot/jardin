"""
app/schemas — Pydantic request/response models for the Jardin API
=================================================================

Distinct from app/models/ which contains SQLAlchemy ORM table definitions.
Import from here rather than from individual schema modules so refactors
only require changing one import line.
"""

from app.schemas.chat import ChatMessage, ChatRequest
from app.schemas.recommendations import (
    RecommendationCreated,
    RecommendationRequest,
    SpeciesOut,
)
from app.schemas.scorer import SpeciesScores

__all__ = [
    "ChatMessage",
    "ChatRequest",
    "RecommendationCreated",
    "RecommendationRequest",
    "SpeciesOut",
    "SpeciesScores",
]
