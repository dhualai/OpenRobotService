"""backfill requirement tag: pjm creator → project, else product"""

from alembic import op
from sqlalchemy import inspect, text

revision = "0005_requirement_tag_project"
down_revision = "0004_requirement_priority_p0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "requirements" not in tables or "users" not in tables:
        return
    bind.execute(
        text(
            "UPDATE requirements r SET tag = 'project' "
            "FROM users u "
            "WHERE r.created_by = u.username AND u.role = 'pjm' "
            "AND (r.tag IS NULL OR btrim(r.tag) = '')"
        )
    )
    bind.execute(
        text(
            "UPDATE requirements SET tag = 'product' "
            "WHERE tag IS NULL OR btrim(tag) = ''"
        )
    )


def downgrade() -> None:
    return
