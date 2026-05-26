import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Recommendation(Base):
    """
    A single species recommendation produced by the agent for a GardenRequest.

    score_breakdown — per-dimension weighted scores, e.g.:
        {"pollinators": 0.8, "insects": 0.6, "soil": 0.4, ...}

    agent_reasoning — one free-text explanation per dimension, e.g.:
        {"pollinators": "Rich in nectar; favoured by bumblebees.", ...}
    """

    __tablename__ = "recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    garden_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("garden_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    species_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("species.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    final_score: Mapped[float] = mapped_column(Float, nullable=False)
    score_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    agent_reasoning: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Convenience relationships
    garden_request = relationship("GardenRequest", backref="recommendations")
    species = relationship("Species", backref="recommendations")
