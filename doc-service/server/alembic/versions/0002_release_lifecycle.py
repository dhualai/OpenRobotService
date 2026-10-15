"""product_releases lifecycle band + progress"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "0002_release_lifecycle"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("product_releases")}
    if "lifecycle_band" not in cols:
        op.add_column(
            "product_releases",
            sa.Column("lifecycle_band", sa.String(16), nullable=False, server_default="dev"),
        )
    if "progress" not in cols:
        op.add_column("product_releases", sa.Column("progress", sa.Float(), nullable=True))
    bind.execute(
        text(
            "UPDATE product_releases SET lifecycle_band = 'joint_test', "
            "progress = COALESCE(progress, 0) "
            "WHERE published_at IS NOT NULL AND lifecycle_band NOT IN ('joint_test', 'done')"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("product_releases")}
    if "progress" in cols:
        op.drop_column("product_releases", "progress")
    if "lifecycle_band" in cols:
        op.drop_column("product_releases", "lifecycle_band")
