"""Add content_hash and priority_size to garden_requests

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-26
"""

from alembic import op
import sqlalchemy as sa

# ---------------------------------------------------------------------------
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # content_hash — used to deduplicate identical requests and skip re-running
    # the agent pipeline when the same location + priorities have already been
    # scored.  Nullable so existing rows are unaffected.
    op.add_column(
        "garden_requests",
        sa.Column("content_hash", sa.String(16), nullable=True),
    )
    op.create_index(
        "ix_garden_requests_content_hash",
        "garden_requests",
        ["content_hash"],
    )

    # priority_size — 6th ecological dimension: space / container suitability.
    # Existing rows default to 0.5 (neutral weight).
    op.add_column(
        "garden_requests",
        sa.Column(
            "priority_size",
            sa.Float,
            nullable=False,
            server_default="0.5",
        ),
    )


def downgrade() -> None:
    op.drop_column("garden_requests", "priority_size")
    op.drop_index("ix_garden_requests_content_hash", table_name="garden_requests")
    op.drop_column("garden_requests", "content_hash")
