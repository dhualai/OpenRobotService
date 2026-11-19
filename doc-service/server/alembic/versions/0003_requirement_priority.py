"""requirements.priority 5-point scale; existing rows default 3"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_requirement_priority"
down_revision = "0002_release_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("requirements")}
    if "priority" not in cols:
        op.add_column(
            "requirements",
            sa.Column("priority", sa.Integer(), nullable=False, server_default="3"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("requirements")}
    if "priority" in cols:
        op.drop_column("requirements", "priority")
