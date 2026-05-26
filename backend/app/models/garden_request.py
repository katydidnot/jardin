import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class GardenRequest(Base):
    """A user's garden planting request with location and ecological priorities."""

    __tablename__ = "garden_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Location
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    region: Mapped[str | None] = mapped_column(String, nullable=True)

    # Ecological priority weights (0–1); agent normalises these at query time
    priority_pollinators: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5
    )
    priority_insects: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5
    )
    priority_soil: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5
    )
    priority_environment: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5
    )
    priority_food_utility: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5
    )
    priority_size: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5
    )

    # 16-char hex hash of normalised request parameters; used to serve cached
    # results without re-running the agent pipeline for identical requests.
    content_hash: Mapped[str | None] = mapped_column(
        String(16), nullable=True, index=True
    )

    free_text_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
