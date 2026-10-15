"""keep requirement review rounds as history"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text

revision = "0011_requirement_review_rounds"
down_revision = "0010_requirement_pending_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    tables = set(insp.get_table_names())
    if "requirement_review_rounds" not in tables:
        op.create_table(
            "requirement_review_rounds",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("requirement_id", sa.String(32), sa.ForeignKey("requirements.id"), nullable=False),
            sa.Column("started_by", sa.String(64), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("outcome", sa.String(16), nullable=True),
        )
    if "requirement_reviewers" not in tables:
        return
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("requirement_reviewers")}
    if "round_id" not in cols:
        op.add_column("requirement_reviewers", sa.Column("round_id", sa.Integer(), nullable=True))
        op.create_foreign_key(
            "requirement_reviewers_round_id_fkey",
            "requirement_reviewers",
            "requirement_review_rounds",
            ["round_id"],
            ["id"],
        )
    bind.execute(
        text(
            """
            INSERT INTO requirement_review_rounds (requirement_id, started_by, started_at, closed_at, outcome)
            SELECT
              r.requirement_id,
              COALESCE(req.updated_by, req.created_by),
              COALESCE(MIN(r.decided_at), req.updated_at, NOW()),
              CASE WHEN COUNT(*) FILTER (WHERE r.verdict IS NULL) = 0 THEN MAX(r.decided_at) ELSE NULL END,
              CASE
                WHEN COUNT(*) FILTER (WHERE r.verdict IS NULL) > 0 THEN NULL
                WHEN COUNT(*) FILTER (WHERE r.verdict IS DISTINCT FROM 'pass') = 0 THEN 'pass'
                ELSE 'fail'
              END
            FROM requirement_reviewers r
            JOIN requirements req ON req.id = r.requirement_id
            WHERE r.round_id IS NULL
            GROUP BY r.requirement_id, req.updated_by, req.created_by, req.updated_at
            """
        )
    )
    bind.execute(
        text(
            """
            UPDATE requirement_reviewers rv
            SET round_id = rd.id
            FROM requirement_review_rounds rd
            WHERE rv.round_id IS NULL AND rd.requirement_id = rv.requirement_id
            """
        )
    )
    bind.execute(text("DELETE FROM requirement_reviewers WHERE round_id IS NULL"))
    insp = inspect(bind)
    for uc in insp.get_unique_constraints("requirement_reviewers"):
        cols = set(uc.get("column_names") or [])
        if cols == {"requirement_id", "username"}:
            op.drop_constraint(uc["name"], "requirement_reviewers", type_="unique")
    insp = inspect(bind)
    have = {tuple(uc.get("column_names") or []) for uc in insp.get_unique_constraints("requirement_reviewers")}
    if ("round_id", "username") not in have and ("username", "round_id") not in have:
        op.create_unique_constraint(
            "requirement_reviewers_round_id_username_key",
            "requirement_reviewers",
            ["round_id", "username"],
        )
    op.alter_column("requirement_reviewers", "round_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    insp = inspect(bind)
    if "requirement_reviewers" in set(insp.get_table_names()):
        for uc in insp.get_unique_constraints("requirement_reviewers"):
            cols = set(uc.get("column_names") or [])
            if cols == {"round_id", "username"}:
                op.drop_constraint(uc["name"], "requirement_reviewers", type_="unique")
        fks = insp.get_foreign_keys("requirement_reviewers")
        for fk in fks:
            if "round_id" in (fk.get("constrained_columns") or []):
                op.drop_constraint(fk["name"], "requirement_reviewers", type_="foreignkey")
        cols = {c["name"] for c in insp.get_columns("requirement_reviewers")}
        if "round_id" in cols:
            op.drop_column("requirement_reviewers", "round_id")
        op.create_unique_constraint(
            "requirement_reviewers_requirement_id_username_key",
            "requirement_reviewers",
            ["requirement_id", "username"],
        )
    if "requirement_review_rounds" in set(inspect(bind).get_table_names()):
        op.drop_table("requirement_review_rounds")
