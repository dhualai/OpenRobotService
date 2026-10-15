"""user display_name"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0008_user_display_name"
down_revision = "0007_hours_version_close"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "users" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "display_name" not in cols:
        op.add_column("users", sa.Column("display_name", sa.String(32), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "users" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("users")}
    if "display_name" in cols:
        op.drop_column("users", "display_name")
