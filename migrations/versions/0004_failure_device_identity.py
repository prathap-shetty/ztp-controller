"""Record reported vendor, platform and model with registration failures."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable: rows recorded before this revision have no reported identity.
    for name in ("vendor", "platform", "model"):
        op.add_column("registration_failures", sa.Column(name, sa.String(128), nullable=True))


def downgrade():
    for name in ("model", "platform", "vendor"):
        op.drop_column("registration_failures", name)
