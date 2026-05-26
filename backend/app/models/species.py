import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Species(Base):
    """A plant species with ecological scoring and semantic embedding."""

    __tablename__ = "species"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scientific_name: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    common_names: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    family: Mapped[str | None] = mapped_column(String, nullable=True)

    # Geographic / growth data
    native_regions: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )  # e.g. ["GB", "FR", "DE"]
    bloom_months: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer), nullable=True
    )  # 1–12
    height_cm_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_cm_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    soil_preferences: Mapped[dict | None] = mapped_column(
        JSONB, nullable=True
    )  # {"ph_min": 5.5, "ph_max": 7.5, "types": ["clay", "loam"]}

    # Ecological scores (0–1)
    pollinator_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    insect_host_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    soil_benefit_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    environmental_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    food_utility_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    food_utility_notes: Mapped[str | None] = mapped_column(String, nullable=True)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # pgvector embedding for semantic search (BAAI/bge-base-en-v1.5 → 768-dim)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
