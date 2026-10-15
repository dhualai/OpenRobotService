"""personal drafts"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0014_drafts"
down_revision = "0013_requirement_revisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "drafts" in set(insp.get_table_names()):
        return
    op.create_table(
        "drafts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body_blob", sa.LargeBinary(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_drafts_owner", "drafts", ["owner"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "drafts" in set(insp.get_table_names()):
        op.drop_index("ix_drafts_owner", table_name="drafts")
        op.drop_table("drafts")
