"""requirement reviewers; draft/review/developing/done"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "0009_requirement_review"
down_revision = "0008_user_display_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "requirement_reviewers" not in tables:
        op.create_table(
            "requirement_reviewers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("requirement_id", sa.String(32), sa.ForeignKey("requirements.id"), nullable=False),
            sa.Column("username", sa.String(64), nullable=False),
            sa.Column("verdict", sa.String(16), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("requirement_id", "username"),
        )
    if "requirements" in tables:
        bind.execute(
            text("UPDATE requirements SET status = 'developing' WHERE status IN ('designing', 'testing')")
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "requirement_reviewers" in set(insp.get_table_names()):
        op.drop_table("requirement_reviewers")
