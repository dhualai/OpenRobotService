"""add learning_answers table

Revision ID: e7a1b3c5d9f1
Revises: b5d9f307ca12
Create Date: 2026-11-23 16:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "e7a1b3c5d9f1"
down_revision = "b5d9f307ca12"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "learning_answers" not in tables:
        op.create_table("learning_answers",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("article_id", sa.String(32), nullable=False),
            sa.Column("article_version", sa.Integer(), nullable=False),
            sa.Column("question_id", sa.String(32), nullable=False),
            sa.Column("answers", sa.JSON(), nullable=False),
            sa.Column("is_correct", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("user_id", "question_id", "article_version", name="uq_learning_answer_once"))
        op.create_index("ix_learning_answers_user_id", "learning_answers", ["user_id"])
        op.create_index("ix_learning_answers_article_id", "learning_answers", ["article_id"])
        op.create_index("ix_learning_answers_question_id", "learning_answers", ["question_id"])
        op.create_index("ix_learning_answers_user_article", "learning_answers", ["user_id", "article_id"])


def downgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "learning_answers" in tables:
        op.drop_table("learning_answers")
