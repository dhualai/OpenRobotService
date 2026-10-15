"""comments on discussion drafts"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0018_draft_comments"
down_revision = "0017_visit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "draft_comments" in set(insp.get_table_names()):
        return
    op.create_table(
        "draft_comments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("draft_id", sa.String(32), sa.ForeignKey("drafts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author", sa.String(64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_draft_comments_draft_id", "draft_comments", ["draft_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "draft_comments" not in set(insp.get_table_names()):
        return
    op.drop_index("ix_draft_comments_draft_id", table_name="draft_comments")
    op.drop_table("draft_comments")
