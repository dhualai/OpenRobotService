from alembic import op
import sqlalchemy as sa


revision = "f2a5b8c1d437"
down_revision = "e1f4a7b0c326"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("knowledge_challenge_answers")}
    for name in ("images", "draft_images"):
        if name not in columns:
            op.add_column("knowledge_challenge_answers", sa.Column(name, sa.JSON(), nullable=True))


def downgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("knowledge_challenge_answers")}
    for name in ("draft_images", "images"):
        if name in columns:
            op.drop_column("knowledge_challenge_answers", name)
