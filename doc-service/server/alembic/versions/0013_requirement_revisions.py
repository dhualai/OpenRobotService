"""requirement document revisions"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0013_requirement_revisions"
down_revision = "0012_version_admin_protected"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "requirement_revisions" in set(insp.get_table_names()):
        return
    if "requirements" not in set(insp.get_table_names()):
        return
    op.create_table(
        "requirement_revisions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("requirement_id", sa.String(32), sa.ForeignKey("requirements.id"), nullable=False),
        sa.Column("body_blob", sa.LargeBinary(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("author", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "requirement_revisions" in set(insp.get_table_names()):
        op.drop_table("requirement_revisions")
