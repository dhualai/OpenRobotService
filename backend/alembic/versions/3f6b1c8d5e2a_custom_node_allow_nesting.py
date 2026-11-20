"""custom_node_allow_nesting

项目增补出来的节点一律 allow_custom = 1：在新节点下面还能接着增补，唯一的边界是层数
（MAX_INFO_DEPTH = 4，第 5 层起报「信息层级最多 4 层」）。

代码侧已改：info_node_service.add_custom_node / import_tree 新建节点写死 allow_custom=True
（旧代码写死 False，加一层就到头）。本迁移把**存量**的项目增补节点补齐——它们本来就是
同一个「增补信息」入口建的，留着 0 等于把用户已经建好的节点锁死（如项目 123 的车型3，
下面加不了任何东西）。

只动 project_id IS NOT NULL 的行：全局模板节点的 allow_custom 是模板作者在「详情模板」
页勾选的决定（哪个位置允许各项目增补），不属于本次口径，一行都不碰。

幂等：只更新 allow_custom = 0 的行，重复执行无副作用。

Revision ID: 3f6b1c8d5e2a
Revises: 9d2f4a6b8c01
Create Date: 2026-09-18
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = '3f6b1c8d5e2a'
down_revision: Union[str, None] = '9d2f4a6b8c01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(text(
        "UPDATE project_info_node SET allow_custom = 1 "
        "WHERE project_id IS NOT NULL AND allow_custom = 0"
    ))
    print(f"[custom_node_allow_nesting] 打开 {result.rowcount} 个存量增补节点的增补开关")


def downgrade() -> None:
    """回到旧口径：项目增补节点一律不许往下加（旧代码新建的节点就是 allow_custom=0）。

    这是一次全量数据归一，回退后管理员后来手工打开过的项目节点也会一起关掉 ——
    旧行为下本来就没有打开它的入口，属可接受的有损回退。
    """
    conn = op.get_bind()
    conn.execute(text(
        "UPDATE project_info_node SET allow_custom = 0 WHERE project_id IS NOT NULL"
    ))
