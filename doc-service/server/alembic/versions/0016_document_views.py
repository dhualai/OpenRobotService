"""document view counts for admins"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0016_document_views"
down_revision = "0015_draft_publish"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "document_views" in set(insp.get_table_names()):
        return
    op.create_table(
        "document_views",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("product", sa.String(8), nullable=False),
        sa.Column("resource_id", sa.String(80), nullable=False),
        sa.Column("version", sa.String(32), nullable=False, server_default=""),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("kind", "product", "resource_id", "version", "username"),
    )
    op.create_index(
        "ix_document_views_lookup",
        "document_views",
        ["kind", "product", "resource_id", "version"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "document_views" not in set(insp.get_table_names()):
        return
    op.drop_index("ix_document_views_lookup", table_name="document_views")
    op.drop_table("document_views")
