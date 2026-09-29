"""Persist M1 planning attempts and scoped status grants."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(128), nullable=False, unique=True),
        sa.Column("serial", sa.String(128), nullable=False, unique=True),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("intent", sa.JSON(), nullable=False),
        sa.Column("observed", sa.JSON(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("state = 'AUTHORIZED'", name="m1_authorized_only"),
    )
    op.create_table(
        "status_grants",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("attempts.id"), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_status_grants_attempt_id", "status_grants", ["attempt_id"])
    op.create_index("ix_status_grants_expires_at", "status_grants", ["expires_at"])
    op.create_table(
        "attempt_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("attempts.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_attempt_events_attempt_id", "attempt_events", ["attempt_id"])
    op.create_table(
        "registration_buckets",
        sa.Column("source_hash", sa.String(64), primary_key=True),
        sa.Column("minute", sa.BigInteger(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("attempt_events")
    op.drop_table("registration_buckets")
    op.drop_table("status_grants")
    op.drop_table("attempts")
