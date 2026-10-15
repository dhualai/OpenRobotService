"""requirements.priority P0–P4 (0 most urgent); remap old 1–5 default 3 → 2"""

from alembic import op
from sqlalchemy import inspect, text

revision = "0004_requirement_priority_p0"
down_revision = "0003_requirement_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("requirements")}
    if "priority" not in cols:
        return
    bind.execute(
        text(
            "UPDATE requirements SET priority = 5 - priority "
            "WHERE priority BETWEEN 1 AND 5"
        )
    )
    bind.execute(text("UPDATE requirements SET priority = 2 WHERE priority < 0 OR priority > 4"))
    op.alter_column("requirements", "priority", server_default="2")


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("requirements")}
    if "priority" not in cols:
        return
    bind.execute(
        text(
            "UPDATE requirements SET priority = 5 - priority "
            "WHERE priority BETWEEN 0 AND 4"
        )
    )
    op.alter_column("requirements", "priority", server_default="3")
