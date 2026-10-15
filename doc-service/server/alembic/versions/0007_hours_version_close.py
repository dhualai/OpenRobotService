"""hour expectations unique includes version; weekly close ledger"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0007_hours_version_close"
down_revision = "0006_hour_expectations"
branch_labels = None
depends_on = None

_WANTED = ("username", "product", "feature_id", "version")
_OLD = ("username", "product", "feature_id")


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "hours_week_closes" not in tables:
        op.create_table(
            "hours_week_closes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("week", sa.String(16), nullable=False),
            sa.Column("username", sa.String(64), nullable=False),
            sa.Column("product", sa.String(8), nullable=False),
            sa.Column("feature_id", sa.String(80), nullable=False),
            sa.Column("version", sa.String(32), nullable=False),
            sa.Column("hours", sa.Float(), nullable=False),
            sa.UniqueConstraint("week", "username", "product", "feature_id", "version"),
        )
    if "hour_expectations" not in tables:
        return
    for c in insp.get_unique_constraints("hour_expectations"):
        if tuple(c["column_names"]) == _OLD:
            op.drop_constraint(c["name"], "hour_expectations", type_="unique")
    insp = inspect(bind)
    have = [tuple(c["column_names"]) for c in insp.get_unique_constraints("hour_expectations")]
    if _WANTED not in have:
        op.create_unique_constraint("uq_hour_exp_user_feat_ver", "hour_expectations", list(_WANTED))


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "hour_expectations" in tables:
        for c in insp.get_unique_constraints("hour_expectations"):
            if tuple(c["column_names"]) == _WANTED:
                op.drop_constraint(c["name"], "hour_expectations", type_="unique")
        insp = inspect(bind)
        have = [tuple(c["column_names"]) for c in insp.get_unique_constraints("hour_expectations")]
        if _OLD not in have:
            op.create_unique_constraint(
                "hour_expectations_username_product_feature_id_key",
                "hour_expectations",
                list(_OLD),
            )
    if "hours_week_closes" in tables:
        op.drop_table("hours_week_closes")
