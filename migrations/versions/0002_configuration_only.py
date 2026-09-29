"""Configuration artifacts and durable validation, without image installation."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("m1_authorized_only", "attempts", type_="check")
    op.create_check_constraint(
        "m2a_states",
        "attempts",
        "state IN ('AUTHORIZED','CONFIGURING','VALIDATING','VALIDATED','FAILED')",
    )
    op.add_column("attempts", sa.Column("config_body", sa.Text(), nullable=True))
    op.add_column("attempts", sa.Column("failure_reason", sa.String(64), nullable=True))
    op.create_table(
        "validation_jobs",
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("attempts.id"), primary_key=True),
        sa.Column("available_at", sa.BigInteger(), nullable=False),
        sa.Column("deadline", sa.BigInteger(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_id", sa.String(36), nullable=True),
        sa.Column("lease_until", sa.BigInteger(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
    )
    op.create_index("ix_validation_jobs_available_at", "validation_jobs", ["available_at"])


def downgrade():
    # Do not rewrite successful/failed M2a history into an M1 authorization record.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT count(*) FROM attempts WHERE state <> 'AUTHORIZED'")):
        raise RuntimeError("Cannot downgrade M2a lifecycle history; restore an M1 backup instead")
    op.drop_table("validation_jobs")
    op.drop_column("attempts", "config_body")
    op.drop_column("attempts", "failure_reason")
    op.drop_constraint("m2a_states", "attempts", type_="check")
    op.create_check_constraint("m1_authorized_only", "attempts", "state = 'AUTHORIZED'")
