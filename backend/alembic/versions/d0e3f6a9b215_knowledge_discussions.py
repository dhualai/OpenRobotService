from alembic import op
import sqlalchemy as sa

revision = "d0e3f6a9b215"
down_revision = "c9d2e5f8a104"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("knowledge_discussions"):
        return
    op.create_table("knowledge_discussions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    for column in ("task_id", "author_id", "created_at"):
        op.create_index(f"ix_knowledge_discussions_{column}", "knowledge_discussions", [column])


def downgrade():
    op.drop_table("knowledge_discussions")
