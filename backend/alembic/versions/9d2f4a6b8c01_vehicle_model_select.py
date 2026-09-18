"""vehicle_model_select

把「硬件 / 车辆」下的车型1 / 车型2 从文本节点改成下拉节点（选项 = AGV 车型目录），
并打开「车辆」的 allow_custom，让各项目能自建车型3 / 车型4。

为什么必须动库而不是改 default.yaml：default.yaml 只在播种迁移（7c1e9a4b2d38）
执行时被读一次，之后模板的权威来源就是库里的全局节点行。改 YAML 对已经建好的库毫无影响。

为什么车型必须变成「值」：车型1/车型2 是全局节点（project_id IS NULL），各项目共用同一行。
型号写进节点标题等于写进全局定义——后端会以 403「全局字段定义请在「详情模板」里修改」拒绝，
而且就算能改，也会把 A 项目的车型改成所有项目的车型。改成下拉值后，型号落在
project_info_value 里，各项目各选各的。

车型目录不在本文件里抄一份：从 info_node_import_service.VEHICLE_MODEL_CODES 取
（后端唯一一份，前端 vehicleModels.ts 与之对应），避免目录出现第三份副本各自漂移。

幂等：只在节点存在、且当前还是 text 时才改；config 已有内容时不覆盖
（管理员可能已经在模板页手工调过选项）。重复执行不会破坏数据。

Revision ID: 9d2f4a6b8c01
Revises: 7c1e9a4b2d38
Create Date: 2026-09-18
"""
import json
from typing import List, Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = '9d2f4a6b8c01'
down_revision: Union[str, None] = '7c1e9a4b2d38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 车型节点 / 车辆节点的 node_key（与 7c1e9a4b2d38 的 TITLE_KEY_MAP 一致）
VEHICLE_PARENT_KEY = 'hardware.vehicle'
VEHICLE_MODEL_KEYS = ('hardware.vehicle.model_1', 'hardware.vehicle.model_2')


def _vehicle_model_codes() -> List[str]:
    """AGV 车型目录的全部型号（按目录顺序），从后端唯一一份目录取。"""
    from app.modules.admin.services.info_node_import_service import VEHICLE_MODEL_CODES

    return list(VEHICLE_MODEL_CODES)


def upgrade() -> None:
    conn = op.get_bind()

    # 1) 车辆：允许各项目在其下增补（车型3 / 车型4 …）
    conn.execute(text(
        "UPDATE project_info_node SET allow_custom = 1 "
        "WHERE project_id IS NULL AND node_key = :key AND allow_custom = 0"
    ), {'key': VEHICLE_PARENT_KEY})

    # 2) 车型N：text → select + 铺上车型目录
    codes = _vehicle_model_codes()
    config = json.dumps({'options': [{'value': code, 'label': code} for code in codes]},
                        ensure_ascii=False)
    for key in VEHICLE_MODEL_KEYS:
        rows = conn.execute(text(
            "SELECT id, value_type, config FROM project_info_node "
            "WHERE project_id IS NULL AND node_key = :key"
        ), {'key': key}).fetchall()
        if not rows:
            continue  # 这套模板里没有车型N（被管理员停用或删过），跳过而不是报错
        for node_id, value_type, existing_config in rows:
            if (value_type or 'text') != 'text':
                continue  # 已经是下拉（含管理员手工改过）——不动它的选项
            conn.execute(text(
                "UPDATE project_info_node SET value_type = 'select', config = :config, "
                "updated_at = :now WHERE id = :id"
            ), {
                'config': config if existing_config is None else existing_config,
                'now': _now_str(),
                'id': node_id,
            })


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def downgrade() -> None:
    """改回文本节点并清掉车型选项（「车辆」的 allow_custom 一并还原）。

    只回退还是 select 的车型节点；管理员后来手工改过类型的不动。
    各项目已经在下拉里选中的型号不会跟着消失（值在 project_info_value 里），
    只是界面上不再有下拉可用——和 upgrade 之前的状态一致。
    """
    conn = op.get_bind()
    for key in VEHICLE_MODEL_KEYS:
        conn.execute(text(
            "UPDATE project_info_node SET value_type = 'text', config = NULL, updated_at = :now "
            "WHERE project_id IS NULL AND node_key = :key AND value_type = 'select'"
        ), {'key': key, 'now': _now_str()})
    conn.execute(text(
        "UPDATE project_info_node SET allow_custom = 0 "
        "WHERE project_id IS NULL AND node_key = :key"
    ), {'key': VEHICLE_PARENT_KEY})
