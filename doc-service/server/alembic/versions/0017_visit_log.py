"""rolling 7-day visit log for admins"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0017_visit_log"
down_revision = "0016_document_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "visit_log" in set(insp.get_table_names()):
        return
    op.create_table(
        "visit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("product", sa.String(8), nullable=True),
        sa.Column("resource_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("version", sa.String(32), nullable=False, server_default=""),
        sa.Column("title", sa.String(200), nullable=False, server_default=""),
    )
    op.create_index("ix_visit_log_at", "visit_log", ["at"])
    op.create_index("ix_visit_log_username", "visit_log", ["username"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "visit_log" not in set(insp.get_table_names()):
        return
    op.drop_index("ix_visit_log_username", table_name="visit_log")
    op.drop_index("ix_visit_log_at", table_name="visit_log")
    op.drop_table("visit_log")
