from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT

revision = "c9d2e5f8a104"
down_revision = "b8c1d4e7f203"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "value_point" not in {column["name"] for column in inspector.get_columns("archive_reports")}:
        op.add_column("archive_reports", sa.Column("value_point", sa.Text(), nullable=True))
    metadata = sa.MetaData()
    sa.Table("knowledge_publications", metadata,
        sa.Column("task_id", sa.BigInteger(), primary_key=True), sa.Column("report_id", sa.BigInteger(), nullable=False),
        sa.Column("questioner_id", sa.String(64), nullable=False, index=True),
        sa.Column("answerer_id", sa.String(64), nullable=False, index=True),
        sa.Column("title", sa.String(255), nullable=False), sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("content", sa.Text().with_variant(LONGTEXT, "mysql"), nullable=False), sa.Column("review_comment", sa.Text(), nullable=False),
        sa.Column("value_point", sa.Text(), nullable=False), sa.Column("first_approved_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("active", sa.Boolean(), nullable=False))
    sa.Table("knowledge_question_likes", metadata,
        sa.Column("task_id", sa.BigInteger(), primary_key=True), sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("active", sa.Boolean(), nullable=False), sa.Column("risk_status", sa.String(20), nullable=False),
        sa.Column("risk_note", sa.Text()), sa.Column("risk_operator", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    sa.Table("knowledge_like_events", metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False, index=True), sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False, index=True))
    sa.Table("archive_review_decisions", metadata,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False, index=True), sa.Column("report_id", sa.BigInteger(), nullable=False, index=True),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("reviewer_id", sa.String(64), nullable=False, index=True),
        sa.Column("outcome", sa.String(20), nullable=False), sa.Column("review_comment", sa.Text(), nullable=False),
        sa.Column("value_point", sa.Text()), sa.Column("content", sa.Text().with_variant(LONGTEXT, "mysql"), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("report_id", "version", name="uq_archive_review_version"))
    sa.Table("knowledge_settlements", metadata,
        sa.Column("month", sa.String(7), primary_key=True), sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("rankings", sa.JSON(), nullable=False),
        sa.Column("audit", sa.JSON(), nullable=False), sa.Column("frozen_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_by", sa.String(64)), sa.Column("reviewed_at", sa.DateTime()), sa.Column("granted_at", sa.DateTime()))
    metadata.create_all(bind, checkfirst=True)


def downgrade():
    for table in ("knowledge_settlements", "archive_review_decisions", "knowledge_like_events",
                  "knowledge_question_likes", "knowledge_publications"):
        op.drop_table(table)
    op.drop_column("archive_reports", "value_point")
