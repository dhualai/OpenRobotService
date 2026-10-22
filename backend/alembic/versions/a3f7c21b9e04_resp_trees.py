"""责任双树三张表：resp_trees / resp_nodes / resp_node_links

双树试做页的树此前只在 localStorage + `.local` JSON 快照里，多份副本合并丢过两次
组织树（2026-10-15 / 10-16）。这三张表让它落库成唯一真相源。

注意：本仓 alembic 链与存量库并不一致，`upgrade head` 可能中途被 Duplicate column
打断，所以 `upgrade()` 首行必须带 `has_table` 守卫 —— 重复执行要安全。
app 启动时 `Base.metadata.create_all` 也会建表（见 app/core/database.py），
存量库 / 远端库用 `backend/scripts/create_resp_trees.py` 显式建。
"""
from alembic import op
import sqlalchemy as sa

revision = "a3f7c21b9e04"
down_revision = "d0e3f6a9b215"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("resp_trees"):
        return

    op.create_table(
        "resp_trees",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_resp_trees_kind", "resp_trees", ["kind"])

    op.create_table(
        "resp_nodes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tree_id", sa.Integer(),
                  sa.ForeignKey("resp_trees.id", ondelete="CASCADE", name="fk_resp_nodes_tree"),
                  nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("keywords", sa.JSON(), nullable=True),
        sa.Column("anchor", sa.Text(), nullable=True),
        sa.Column("owners", sa.JSON(), nullable=True),
        sa.Column("linked_node_id", sa.Integer(), nullable=True),
        sa.Column("node_kind", sa.String(16), nullable=False, server_default="leaf"),
        sa.Column("org_kind", sa.String(16), nullable=True),
        sa.Column("vehicle_models", sa.String(255), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("legacy_ref", sa.JSON(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_resp_nodes_tree_parent", "resp_nodes", ["tree_id", "parent_id"])
    op.create_index("ix_resp_nodes_linked_node_id", "resp_nodes", ["linked_node_id"])

    op.create_table(
        "resp_node_links",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(50), nullable=False),
        sa.Column("node_id", sa.Integer(),
                  sa.ForeignKey("resp_nodes.id", ondelete="CASCADE", name="fk_resp_links_node"),
                  nullable=False),
        sa.Column("link_type", sa.String(16), nullable=False, server_default="solid"),
        sa.Column("role", sa.String(16), nullable=False, server_default="member"),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "node_id", name="uq_resp_link_user_node"),
    )
    op.create_index("ix_resp_node_links_node_id", "resp_node_links", ["node_id"])


def downgrade():
    op.drop_table("resp_node_links")
    op.drop_table("resp_nodes")
    op.drop_table("resp_trees")
