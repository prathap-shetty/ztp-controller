"""Bounded, redacted registration diagnostics."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "registration_failures",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("serial", sa.String(128), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("message", sa.String(256), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_registration_failures_created_at", "registration_failures", ["created_at"])


def downgrade():
    op.drop_table("registration_failures")
