from alembic import op
import sqlalchemy as sa


revision = "e1f4a7b0c326"
down_revision = "d0e3f6a9b215"
branch_labels = None
depends_on = None


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    if "knowledge_challenges" not in existing:
        op.create_table("knowledge_challenges",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("task_id", sa.BigInteger(), nullable=True, unique=True),
            sa.Column("request_key", sa.String(64), nullable=False, unique=True),
            sa.Column("creation_fingerprint", sa.String(64), nullable=False),
            sa.Column("publisher_id", sa.String(64), nullable=False),
            sa.Column("questioner_id", sa.String(64), nullable=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("bounty_fen", sa.Integer(), nullable=False),
            sa.Column("ends_at", sa.DateTime(), nullable=False),
            sa.Column("first_published_at", sa.DateTime(), nullable=True),
            sa.Column("asked_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("state", sa.String(24), nullable=False),
            sa.Column("hidden", sa.Boolean(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("rule_version", sa.String(32), nullable=False),
            sa.Column("default_answer_id", sa.Integer(), nullable=True),
            sa.Column("best_answer_id", sa.Integer(), nullable=True),
            sa.Column("settled_at", sa.DateTime(), nullable=True),
            sa.Column("snapshot", sa.JSON(), nullable=True),
            sa.Column("reward_attention", sa.Boolean(), nullable=False),
            sa.Column("last_error", sa.Text(), nullable=True))
        for column in ("ends_at", "state"):
            op.create_index(f"ix_knowledge_challenges_{column}", "knowledge_challenges", [column])
    if "knowledge_challenge_answers" not in existing:
        op.create_table("knowledge_challenge_answers",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("challenge_id", sa.Integer(), nullable=False),
            sa.Column("author_id", sa.String(64), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("draft_content", sa.Text(), nullable=True),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("hidden", sa.Boolean(), nullable=False),
            sa.Column("history", sa.JSON(), nullable=False),
            sa.UniqueConstraint("challenge_id", "author_id", name="uq_challenge_answer_author"))
        op.create_index("ix_knowledge_challenge_answers_challenge_id", "knowledge_challenge_answers", ["challenge_id"])
    if "knowledge_challenge_likes" not in existing:
        op.create_table("knowledge_challenge_likes",
            sa.Column("target_kind", sa.String(16), primary_key=True),
            sa.Column("target_id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.String(64), primary_key=True),
            sa.Column("active", sa.Boolean(), nullable=False))
    if "knowledge_challenge_like_events" not in existing:
        op.create_table("knowledge_challenge_like_events",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("target_kind", sa.String(16), nullable=False),
            sa.Column("target_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False))
        for column in ("user_id", "created_at"):
            op.create_index(f"ix_knowledge_challenge_like_events_{column}", "knowledge_challenge_like_events", [column])
    if "knowledge_challenge_audits" not in existing:
        op.create_table("knowledge_challenge_audits",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("challenge_id", sa.Integer(), nullable=False),
            sa.Column("operator_id", sa.String(64), nullable=False),
            sa.Column("action", sa.String(32), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("before", sa.JSON(), nullable=False),
            sa.Column("after", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False))
        op.create_index("ix_knowledge_challenge_audits_challenge_id", "knowledge_challenge_audits", ["challenge_id"])


def downgrade():
    for table in ("knowledge_challenge_audits", "knowledge_challenge_like_events",
                  "knowledge_challenge_likes", "knowledge_challenge_answers", "knowledge_challenges"):
        op.drop_table(table)
