"""全局信息节点播种测试 —— 不连库（SessionLocal 用假 session 顶掉）。

测的是「节点不能变」这件事本身：
  1. TITLE_KEY_MAP 与 default.yaml 完全对齐（新增节点忘登记 key 时这里先炸，
     而不是等到某个空库启动时才报错）；
  2. build_seed_rows 播出的树：node_key 唯一、层级/排序/类型正确、id 与开发库一致
     （确定性 UUIDv5，所以能拿已知 id 钉住跨环境一致性）；
  3. 车型1/车型2 是下拉 + 车型目录选项（迁移 9d2f4a6b8c01 / 4a7c2e9d1b53 的终态），
     所有节点 allow_custom=True（迁移 5b8e3f2a9c47 的终态）；
  4. ensure_global_info_nodes 只在空库播种，已有全局节点时一行都不动；
  5. 改名只改 node_name（id 按老路径推，见 _ID_PATH_ALIASES），新增节点各就各位
     —— 迁移 6f2c8a1d9b47 的终态。

运行方式（反射 runner；**必须先 import app.core.db**——conftest 会把 create_engine
换成 MagicMock，若任由 admin 包在之后懒加载 app.core.db，event.listen 会对
MagicMock 引擎报 InvalidRequestError）：
    python -c "
    import app.core.db
    import tests.conftest
    import tests.test_info_node_seed_service as t
    n=0
    for name in dir(t):
        if name.startswith('test_'):
            getattr(t, name)(); n+=1
    print('PASS', n)
    "
"""
from unittest.mock import MagicMock

import app.modules.admin.services.info_node_seed_service as seed_mod
from app.modules.admin.services.info_node_seed_service import (
    TITLE_KEY_MAP,
    _flatten_nodes,
    _seed_node_id,
    build_seed_rows,
    ensure_global_info_nodes,
)


# 开发库（helpdesk）里这两行的 id：新环境必须播出一模一样的 id。
# 「车辆」已改名「车型信息」（迁移 6f2c8a1d9b47 只改 node_name）—— id 不变，
# 所以这里按**现路径**取，期望的还是老库那两串。
_DEV_DB_IDS = {
    '基础信息': '2a3eb416-8bfc-5f3b-82d7-1b53597cdba4',
    '硬件/车型信息/车型1': '12d26a80-0f98-5179-b6f8-cf64b15909d2',
    '硬件/车型信息/车型2/数量': '7406af90-9fc8-5ec6-90f9-3015183fc1d0',
}


def _by_key(rows):
    return {row.node_key: row for row in rows}


def test_title_key_map_covers_yaml():
    """yaml 的每条路径都登记了 node_key，key 表里也没有多余的条目。

    新增/改名节点忘了登记 key 时，这里先炸——不用等到某个空库启动时才报错。
    """
    from app.modules.admin.services.project_service import get_info_nodes_template_from_yaml

    paths = {'/'.join(path) for path, _ in _flatten_nodes(get_info_nodes_template_from_yaml())}
    assert paths, 'default.yaml 应该能读出节点'
    assert paths - set(TITLE_KEY_MAP) == set(), 'yaml 里有节点没登记 node_key'
    assert set(TITLE_KEY_MAP) - paths == set(), 'TITLE_KEY_MAP 里有对不上 yaml 的条目'


def test_seed_rows_shape():
    rows = build_seed_rows()
    assert len(rows) > 0, 'default.yaml 应该能播出节点'

    # 全部是全局节点，id / node_key 不重复
    assert all(row.project_id is None for row in rows)
    assert len({row.id for row in rows}) == len(rows)
    assert len({row.node_key for row in rows}) == len(rows)
    assert all(row.status == 'active' and row.allow_custom for row in rows)

    # 层级：根没有父，其余都指向已出现的父；根节点类型为 root
    seen = set()
    for row in rows:
        if row.node_type == 'root':
            assert row.parent_id is None
        else:
            assert row.parent_id in seen, f'{row.node_name} 的父不在它前面'
        seen.add(row.id)

    # 排序：同父下从 10 起、间隔 10
    by_parent = {}
    for row in rows:
        by_parent.setdefault(row.parent_id, []).append(row.sort_order)
    for orders in by_parent.values():
        assert sorted(orders) == [10 * (i + 1) for i in range(len(orders))]


def test_seed_ids_match_dev_db():
    """确定性 id：同一份 default.yaml 在任何环境播出同一批 id。"""
    for path_key, expected in _DEV_DB_IDS.items():
        assert _seed_node_id(path_key) == expected


def test_renamed_path_keeps_legacy_id():
    """改名不改 id：现路径与老路径推出同一串（老库那几行的 id 就是按老路径播的）。"""
    assert _seed_node_id('硬件/车型信息') == _seed_node_id('硬件/车辆')
    assert _seed_node_id('硬件/车型信息/车型1/数量') == _seed_node_id('硬件/车辆/车型1/数量')
    # 没改过名的路径不受影响：别名表只对登记过的路径生效
    assert _seed_node_id('硬件/载具类型') != _seed_node_id('硬件/车型信息')


def test_new_nodes_in_place():
    """2026-09-21 新增/改名的节点：位置、类型与同级排序（迁移 6f2c8a1d9b47 的终态）。"""
    rows = _by_key(build_seed_rows())
    parent_of = {key: row.parent_id for key, row in rows.items()}

    # 基础信息：新三条在前，原来的从 客户信息 起整体后移
    assert parent_of['base.project_code'] == rows['base'].id
    assert parent_of['base.order_no'] == rows['base'].id
    assert parent_of['base.time_summary'] == rows['base'].id
    assert parent_of['base.time_summary.created_at'] == rows['base.time_summary'].id
    assert parent_of['base.time_summary.entry_date'] == rows['base.time_summary'].id
    assert rows['base.project_code'].node_type == 'field'
    assert rows['base.time_summary'].node_type == 'group'
    base_order = [row.node_key for row in sorted(
        (row for row in rows.values() if row.parent_id == rows['base'].id),
        key=lambda row: row.sort_order,
    )]
    assert base_order[:4] == ['base.project_code', 'base.order_no', 'base.time_summary', 'base.customer_info']
    assert rows['base.time_summary.created_at'].sort_order == 10
    assert rows['base.time_summary.agv_offline_date'].sort_order == 40

    # 硬件：车辆（现名「车型信息」）下首个子节点是总车数，车型1/车型2 顺次后移
    vehicle_children = sorted(
        (row for row in rows.values() if row.parent_id == rows['hardware.vehicle'].id),
        key=lambda row: row.sort_order,
    )
    assert [row.node_key for row in vehicle_children] == [
        'hardware.vehicle.total_count',
        'hardware.vehicle.model_1',
        'hardware.vehicle.model_2',
    ]
    assert rows['hardware.vehicle'].node_name == '车型信息'
    assert rows['hardware.vehicle.total_count'].node_type == 'field'
    assert rows['hardware.vehicle.total_count'].value_type == 'text'

    # 调度软件/版本：版本号排在既有两项之后
    version_children = sorted(
        (row for row in rows.values() if row.parent_id == rows['dispatch_software.version'].id),
        key=lambda row: row.sort_order,
    )
    assert [row.node_key for row in version_children] == [
        'dispatch_software.version.submodule',
        'dispatch_software.version.common_config',
        'dispatch_software.version.number',
    ]
    assert rows['dispatch_software.version.number'].sort_order == 30


def test_vehicle_nodes_are_select_with_catalog():
    """车型1/车型2 是下拉，选项=代码里的车型目录（66 款）；下面的「数量」照旧是文字。"""
    from app.modules.admin.services.info_node_import_service import VEHICLE_MODEL_CODES

    rows = _by_key(build_seed_rows())
    for key in ('hardware.vehicle.model_1', 'hardware.vehicle.model_2'):
        node = rows[key]
        assert node.value_type == 'select'
        options = (node.config or {}).get('options') or []
        assert [opt['value'] for opt in options] == list(VEHICLE_MODEL_CODES)
        assert node.node_type == 'group'  # 还带着「数量」子节点

    quantity = rows['hardware.vehicle.model_1.quantity']
    assert quantity.value_type == 'text' and quantity.node_type == 'field'


def test_ensure_skips_when_global_nodes_exist():
    """已有全局节点：一行都不动（管理员改过/停用过的节点必须原样留着）。"""
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.count.return_value = 5
    original, seed_mod.SessionLocal = seed_mod.SessionLocal, lambda: fake_db
    try:
        assert ensure_global_info_nodes() == 0
        fake_db.add_all.assert_not_called()
    finally:
        seed_mod.SessionLocal = original


def test_ensure_seeds_empty_db():
    """空库：整棵树一次写入。"""
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.count.return_value = 0
    original, seed_mod.SessionLocal = seed_mod.SessionLocal, lambda: fake_db
    try:
        count = ensure_global_info_nodes()
        assert count == len(build_seed_rows())
        rows = fake_db.add_all.call_args[0][0]
        assert len(rows) == count
    finally:
        seed_mod.SessionLocal = original
