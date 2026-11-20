from alembic import op
import sqlalchemy as sa


revision = "b5d9f307ca12"
down_revision = "a4c8e2f6b901"
branch_labels = None
depends_on = None


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "learning_articles" not in tables:
        op.create_table("learning_articles",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("title", sa.String(255), nullable=False),
            sa.Column("category", sa.String(80), nullable=False),
            sa.Column("draft", sa.JSON(none_as_null=True), nullable=True),
            sa.Column("sources", sa.JSON(), nullable=False),
            sa.Column("active_version", sa.Integer(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_by", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False))
        op.create_index("ix_learning_articles_updated_at", "learning_articles", ["updated_at"])
    if "learning_article_versions" not in tables:
        op.create_table("learning_article_versions",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("article_id", sa.String(32), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("snapshot", sa.JSON(), nullable=False),
            sa.Column("confirmed_by", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("article_id", "version", name="uq_learning_article_version"))
        op.create_index("ix_learning_article_versions_article_id", "learning_article_versions", ["article_id"])
    if "learning_questions" not in tables:
        op.create_table("learning_questions",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("article_id", sa.String(32), nullable=False),
            sa.Column("article_version", sa.Integer(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("review_status", sa.String(24), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False))
        op.create_index("ix_learning_questions_article_id", "learning_questions", ["article_id"])
        op.create_index("ix_learning_questions_review_status", "learning_questions", ["review_status"])
    if "learning_generations" not in tables:
        op.create_table("learning_generations",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("article_id", sa.String(32), nullable=False),
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.Column("input_revision", sa.Integer(), nullable=False),
            sa.Column("article_version", sa.Integer(), nullable=False),
            sa.Column("operator_id", sa.String(64), nullable=False),
            sa.Column("state", sa.String(24), nullable=False),
            sa.Column("result", sa.JSON(), nullable=True),
            sa.Column("error", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False))
        op.create_index("ix_learning_generations_article_id", "learning_generations", ["article_id"])
    if "learning_audits" not in tables:
        op.create_table("learning_audits",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("article_id", sa.String(32), nullable=False),
            sa.Column("question_id", sa.String(32), nullable=True),
            sa.Column("operator_id", sa.String(64), nullable=False),
            sa.Column("action", sa.String(40), nullable=False),
            sa.Column("snapshot", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False))
        op.create_index("ix_learning_audits_article_id", "learning_audits", ["article_id"])
    if "permissions" in tables:
        permissions = sa.table("permissions", sa.column("id"), sa.column("code"), sa.column("name"),
            sa.column("description"), sa.column("resource_type"), sa.column("action"), sa.column("enabled"))
        code = "frontend:admin:learning:manage"
        if op.get_bind().execute(sa.select(permissions.c.id).where(permissions.c.code == code)).first() is None:
            op.get_bind().execute(permissions.insert().values(id="perm_learning_manage", code=code,
                name="题目生成与维护", description="后台运营与数据中的知识点与题库管理",
                resource_type="frontend", action="manage", enabled="true"))


def downgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "permissions" in tables:
        permissions = sa.table("permissions", sa.column("id"), sa.column("code"))
        ids = sa.select(permissions.c.id).where(permissions.c.code == "frontend:admin:learning:manage")
        if "role_permissions" in tables:
            relations = sa.table("role_permissions", sa.column("permission_id"))
            op.get_bind().execute(relations.delete().where(relations.c.permission_id.in_(ids)))
        op.get_bind().execute(permissions.delete().where(permissions.c.code == "frontend:admin:learning:manage"))
    for table in ("learning_audits", "learning_generations", "learning_questions", "learning_article_versions", "learning_articles"):
        if table in tables:
            op.drop_table(table)
