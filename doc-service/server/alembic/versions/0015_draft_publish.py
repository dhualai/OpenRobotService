"""draft publish audience"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0015_draft_publish"
down_revision = "0014_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "drafts" not in tables:
        return
    cols = {c["name"] for c in insp.get_columns("drafts")}
    if "status" not in cols:
        op.add_column("drafts", sa.Column("status", sa.String(16), nullable=False, server_default="draft"))
    if "audience" not in cols:
        op.add_column("drafts", sa.Column("audience", sa.String(16), nullable=False, server_default="listed"))
    if "draft_viewers" not in tables:
        op.create_table(
            "draft_viewers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("draft_id", sa.String(32), sa.ForeignKey("drafts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("username", sa.String(64), nullable=False),
            sa.UniqueConstraint("draft_id", "username"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "draft_viewers" in tables:
        op.drop_table("draft_viewers")
    if "drafts" in tables:
        cols = {c["name"] for c in insp.get_columns("drafts")}
        if "audience" in cols:
            op.drop_column("drafts", "audience")
        if "status" in cols:
            op.drop_column("drafts", "status")
