"""module version admin_protected"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0012_version_admin_protected"
down_revision = "0011_requirement_review_rounds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "module_versions" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("module_versions")}
    if "admin_protected" not in cols:
        op.add_column(
            "module_versions",
            sa.Column("admin_protected", sa.Boolean(), nullable=False, server_default="false"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "module_versions" not in set(insp.get_table_names()):
        return
    cols = {c["name"] for c in insp.get_columns("module_versions")}
    if "admin_protected" in cols:
        op.drop_column("module_versions", "admin_protected")
