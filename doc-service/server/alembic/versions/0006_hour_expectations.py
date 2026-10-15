"""per-user expected hours"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0006_hour_expectations"
down_revision = "0005_requirement_tag_project"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "hour_expectations" in tables:
        return
    op.create_table(
        "hour_expectations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("product", sa.String(8), nullable=False),
        sa.Column("feature_id", sa.String(80), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("expected_hours", sa.Float(), nullable=False),
        sa.UniqueConstraint("username", "product", "feature_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "hour_expectations" in set(inspect(bind).get_table_names()):
        op.drop_table("hour_expectations")
