"""all_nodes_allow_custom

所有节点都允许各项目在其下增补信息：`allow_custom` 全量置 1。

2026-09-18 用户要求「把详情模板里的『允许各项目在此节点下增补信息』去掉，所有节点都
默认可以增加」：勾选框从「详情模板」页删除，`allow_custom` 退出闸门角色（`add_custom_node`
不再读它），增补的唯一边界是层数（MAX_INFO_DEPTH = 4）。

代码侧已改：`normalize_template_nodes` 一律写 true、`add_custom_node` / `import_tree`
新建节点写死 true。本迁移把**存量**里还停在 0 的全局模板节点补齐——它们大多是勾选框
时代没被勾上的位置，留着 0 只会与「所有节点都能加」的规则互相矛盾（当前 106 行）。

项目增补节点（project_id IS NOT NULL）在 3f6b1c8d5e2a 已全部打开，这里一并兜住。

幂等：只更新 allow_custom = 0 的行，重复执行无副作用。

Revision ID: 5b8e3f2a9c47
Revises: 4a7c2e9d1b53
Create Date: 2026-09-18
"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = '5b8e3f2a9c47'
down_revision: Union[str, None] = '4a7c2e9d1b53'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(text(
        "UPDATE project_info_node SET allow_custom = 1 WHERE allow_custom = 0"
    ))
    print(f"[all_nodes_allow_custom] 打开 {result.rowcount} 个存量节点的增补开关")


def downgrade() -> None:
    """回到勾选框时代：全局节点默认不允许增补。

    逐节点的旧值没有留档（谁被勾过不可复原），只能把全局节点整体置回 0 ——
    旧行为下「修改 allow_custom」本来就只能靠「详情模板」页逐个勾选，属可接受的有损回退。
    项目增补节点保持 1（3f6b1c8d5e2a 的口径，与本次无关）。
    """
    conn = op.get_bind()
    conn.execute(text(
        "UPDATE project_info_node SET allow_custom = 0 WHERE project_id IS NULL"
    ))
