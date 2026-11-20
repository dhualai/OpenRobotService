from alembic import op
import sqlalchemy as sa


revision = "a4c8e2f6b901"
down_revision = "f2a5b8c1d437"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("knowledge_challenge_answers")}
    for name in ("attachments", "draft_attachments"):
        if name not in columns:
            op.add_column("knowledge_challenge_answers", sa.Column(name, sa.JSON(), nullable=True))
    if "knowledge_challenge_attachments" not in inspector.get_table_names():
        op.create_table("knowledge_challenge_attachments",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("challenge_id", sa.Integer(), nullable=False),
            sa.Column("uploader_id", sa.String(64), nullable=False),
            sa.Column("filename", sa.String(255), nullable=False),
            sa.Column("size", sa.Integer(), nullable=False),
            sa.Column("content_type", sa.String(128), nullable=False),
            sa.Column("object_path", sa.String(512), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False))
        op.create_index("ix_knowledge_challenge_attachments_challenge_id",
            "knowledge_challenge_attachments", ["challenge_id"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if "knowledge_challenge_attachments" in inspector.get_table_names():
        op.drop_table("knowledge_challenge_attachments")
    columns = {column["name"] for column in inspector.get_columns("knowledge_challenge_answers")}
    for name in ("draft_attachments", "attachments"):
        if name in columns:
            op.drop_column("knowledge_challenge_answers", name)
