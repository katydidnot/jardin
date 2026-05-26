"""Initial schema: species, garden_requests, recommendations

Revision ID: 0001
Revises:
Create Date: 2026-05-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None
# ---------------------------------------------------------------------------


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # pgvector extension — safe to run even if it already exists           #
    # ------------------------------------------------------------------ #
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------ #
    # species                                                              #
    # ------------------------------------------------------------------ #
    op.create_table(
        "species",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("scientific_name", sa.String(), nullable=False),
        sa.Column("common_names", postgresql.ARRAY(sa.String()), nullable=True),
        sa.Column("family", sa.String(), nullable=True),
        sa.Column("native_regions", postgresql.JSONB(), nullable=True),
        sa.Column("bloom_months", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("height_cm_min", sa.Integer(), nullable=True),
        sa.Column("height_cm_max", sa.Integer(), nullable=True),
        sa.Column("soil_preferences", postgresql.JSONB(), nullable=True),
        sa.Column("pollinator_score", sa.Float(), nullable=True),
        sa.Column("insect_host_score", sa.Float(), nullable=True),
        sa.Column("soil_benefit_score", sa.Float(), nullable=True),
        sa.Column("environmental_score", sa.Float(), nullable=True),
        sa.Column("food_utility_score", sa.Float(), nullable=True),
        sa.Column("food_utility_notes", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        # embedding added below via raw SQL (vector type not in SA dialect)
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Add the vector column and its approximate-nearest-neighbour index
    # BAAI/bge-base-en-v1.5 produces 768-dim vectors via sentence-transformers
    op.execute("ALTER TABLE species ADD COLUMN embedding vector(768)")
    op.create_index(
        "ix_species_scientific_name",
        "species",
        ["scientific_name"],
        unique=True,
    )
    # IVFFlat index for cosine-similarity search; lists=100 is a good
    # starting point for up to ~1 M rows — tune as the dataset grows.
    op.execute(
        "CREATE INDEX ix_species_embedding_ivfflat "
        "ON species USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )

    # ------------------------------------------------------------------ #
    # garden_requests                                                      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "garden_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("country_code", sa.String(10), nullable=True),
        sa.Column("region", sa.String(), nullable=True),
        sa.Column(
            "priority_pollinators", sa.Float(), nullable=False, server_default="0.5"
        ),
        sa.Column(
            "priority_insects", sa.Float(), nullable=False, server_default="0.5"
        ),
        sa.Column(
            "priority_soil", sa.Float(), nullable=False, server_default="0.5"
        ),
        sa.Column(
            "priority_environment", sa.Float(), nullable=False, server_default="0.5"
        ),
        sa.Column(
            "priority_food_utility", sa.Float(), nullable=False, server_default="0.5"
        ),
        sa.Column("free_text_description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ------------------------------------------------------------------ #
    # recommendations                                                      #
    # ------------------------------------------------------------------ #
    op.create_table(
        "recommendations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "garden_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("garden_requests.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "species_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("species.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("final_score", sa.Float(), nullable=False),
        sa.Column("score_breakdown", postgresql.JSONB(), nullable=True),
        sa.Column("agent_reasoning", postgresql.JSONB(), nullable=True),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_recommendations_garden_request_id",
        "recommendations",
        ["garden_request_id"],
    )
    op.create_index(
        "ix_recommendations_species_id",
        "recommendations",
        ["species_id"],
    )


def downgrade() -> None:
    op.drop_table("recommendations")
    op.drop_table("garden_requests")
    op.execute("DROP INDEX IF EXISTS ix_species_embedding_ivfflat")
    op.drop_table("species")
    # Leave the vector extension in place; removing it could affect other schemas.
