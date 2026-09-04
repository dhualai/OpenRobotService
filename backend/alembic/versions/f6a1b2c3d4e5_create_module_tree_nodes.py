"""create_module_tree_nodes

新增责任模块树"功能级行模型"表 `module_tree_nodes`（每功能一行）。
用途：解决多人并发编辑同一产品不同功能时互相覆盖的问题；并支撑后续
从"整树 JSON + config 回写"演进为"行模型 + AI 内存缓存"。

列说明见 backend/app/models/module_tree_node.py。

Revision ID: f6a1b2c3d4e5
Revises: 1a2b3c4d5e6f
Create Date: 2026-08-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = 'f6a1b2c3d4e5'
down_revision: Union[str, None] = '1a2b3c4d5e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    conn = op.get_bind()
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :name"
        ),
        {"name": table_name},
    ).scalar()
    return bool(row)


def upgrade() -> None:
    if _table_exists("module_tree_nodes"):
        return  # 已存在（可能由 create_all 自动建），幂等跳过

    op.create_table(
        "module_tree_nodes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, comment="主键"),
        sa.Column("product", sa.String(64), nullable=False, comment="产品名"),
        sa.Column("iface_name", sa.String(128), nullable=False, comment="界面名"),
        sa.Column("iface_order", sa.Integer(), nullable=False, server_default="0", comment="界面排列序号"),
        sa.Column("func_name", sa.String(128), nullable=False, comment="功能名"),
        sa.Column("func_order", sa.Integer(), nullable=False, server_default="0", comment="界面内功能排列序号"),
        sa.Column("keywords", sa.JSON(), nullable=True, comment="关键词数组"),
        sa.Column("anchor", sa.Text(), nullable=True, comment="功能描述/锚文本"),
        sa.Column("engineers", sa.JSON(), nullable=True, comment="负责工程师 id 数组"),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), comment="最后更新时间"),
        sa.UniqueConstraint("product", "func_name", name="uq_module_tree_nodes_product_funcname"),
    )
    # 产品名索引（聚合按 product 分组查询）
    op.create_index("ix_module_tree_nodes_product", "module_tree_nodes", ["product"])


def downgrade() -> None:
    if _table_exists("module_tree_nodes"):
        op.drop_index("ix_module_tree_nodes_product", table_name="module_tree_nodes")
        op.drop_table("module_tree_nodes")
