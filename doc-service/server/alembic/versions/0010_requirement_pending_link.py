"""pending_link after review; developing only after a feature is linked"""

from alembic import op
from sqlalchemy import inspect, text

revision = "0010_requirement_pending_link"
down_revision = "0009_requirement_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "requirements" not in tables:
        return
    if "requirement_feature_links" in tables:
        bind.execute(
            text(
                """
                UPDATE requirements SET status = 'pending_link'
                WHERE status = 'developing'
                  AND NOT EXISTS (
                    SELECT 1 FROM requirement_feature_links l
                    WHERE l.requirement_id = requirements.id
                  )
                """
            )
        )
        bind.execute(
            text(
                """
                UPDATE requirements SET status = 'pending_link'
                WHERE status = 'done'
                  AND NOT EXISTS (
                    SELECT 1 FROM requirement_feature_links l
                    WHERE l.requirement_id = requirements.id
                  )
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "requirements" not in set(insp.get_table_names()):
        return
    bind.execute(text("UPDATE requirements SET status = 'developing' WHERE status = 'pending_link'"))
