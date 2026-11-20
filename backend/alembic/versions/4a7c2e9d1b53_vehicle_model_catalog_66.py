"""vehicle_model_catalog_66

车型目录从 50 款换成 66 款（用户 2026-09-18 给定）：删掉 XS1161（改名 XS1201），
新增 UHX-01、XC1031、XCS101U、XFC001、XFC002、XCD0051、XCD062、XCD202Y、XPA152、
XP1153、XQC163、XFL151E、XFL301、XFL351、XORD1、XORD3。

为什么必须动库：车型1/车型2 是全局节点，下拉选项存在节点行的 config.options 里。
代码里的 VEHICLE_MODEL_CODES 只影响「填入车型目录」按钮和导入提示词，
已经建好的库下拉里还是旧的 50 款——不改库等于没改。

幂等：直接整段覆盖 options，型号清单的权威来源就是代码里的 VEHICLE_MODEL_CODES；
重复执行结果一致。（迁移前的库里 options 恰好等于旧清单，没有管理员手工加过的选项，
覆盖不会丢东西；即便有，用户给的 66 款就是全量口径。）

Revision ID: 4a7c2e9d1b53
Revises: 3f6b1c8d5e2a
Create Date: 2026-09-18
"""
import json
from typing import List, Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = '4a7c2e9d1b53'
down_revision: Union[str, None] = '3f6b1c8d5e2a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MODEL_KEYS = ('hardware.vehicle.model_1', 'hardware.vehicle.model_2')

# 回退用：9d2f4a6b8c01 当时铺进库的 50 款（那时候的目录版本），不 import 现在的代码取，
# 否则将来再改目录会把 downgrade 也一起改掉，回退就不回去了。
OLD_CODES: List[str] = [
    'XC1051', 'XC1061', 'XCD031', 'XCD061', 'XCD101', 'XCD151', 'XCD301', 'XCD501',
    'EXP15', 'RPG201', 'XPC151', 'XPG151', 'XSG121',
    'XCF101', 'XP1151', 'XP1152', 'XP1201', 'XP3201',
    'XPL201', 'XPL201P', 'XPL201T', 'XPL301', 'XPL501',
    'XFL201', 'XNA101', 'XNA121', 'XNA151', 'XQE151',
    'XS1151', 'XS1152', 'XS1161', 'XS2201',
    'XSC081', 'XSC121', 'XSC151', 'XSC201', 'XSF101',
    'XQC161', 'XQC201', 'XQE122', 'XQS151', 'XQS181',
    'XCART', 'XCT201', 'XTD401', 'XTD601',
    'XCU0051',
    'XCB031', 'XCL0051', 'XCO0051',
]


def _options_json(codes: List[str]) -> str:
    """与 9d2f4a6b8c01 同一种写法：[{"value": 型号, "label": 型号}]（读路径两种都认）。"""
    return json.dumps({'options': [{'value': code, 'label': code} for code in codes]},
                      ensure_ascii=False)


def _overwrite(codes: List[str], note: str) -> None:
    conn = op.get_bind()
    config = _options_json(codes)
    for key in MODEL_KEYS:
        rows = conn.execute(text(
            "SELECT id, config FROM project_info_node "
            "WHERE project_id IS NULL AND node_key = :key"
        ), {'key': key}).fetchall()
        if not rows:
            continue  # 这套模板里没有车型N（被停用或删过），跳过而不是报错
        for node_id, existing in rows:
            if existing is None:
                continue  # 不是下拉节点（从没被迁移改过），不硬塞选项
            conn.execute(text(
                "UPDATE project_info_node SET config = :config, updated_at = :now WHERE id = :id"
            ), {'config': config, 'now': _now_str(), 'id': node_id})
    print(f"[vehicle_model_catalog_66] {note}：{len(codes)} 款")


def upgrade() -> None:
    from app.modules.admin.services.info_node_import_service import VEHICLE_MODEL_CODES
    _overwrite(list(VEHICLE_MODEL_CODES), '车型目录已更新')


def downgrade() -> None:
    _overwrite(OLD_CODES, '车型目录已回退')


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
