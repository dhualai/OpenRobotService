"""base_new_nodes_and_vehicle_rename

基础模板新增 项目编号 / 订单号 / 时间信息汇总（+5 个子节点）/ 总车数 / 版本号，
并把「硬件 / 车辆」改名为「硬件 / 车型信息」。

为什么必须动库：全局节点行只在播种迁移 7c1e9a4b2d38 那次从 default.yaml 读过一次，
之后模板的权威来源就是库里的行（「详情模板」页改的也是它）。此后改 default.yaml 对
已有库**没有任何影响** —— 不写这条迁移，老库永远不会有「项目编号」这些节点，
台账同步/文件导入也就匹配不上同名的列。

2026-09-21 用户要求（本项目的主要诉求）：基础信息树增加 项目编号 / 订单号 /
时间信息汇总（项目创建时间 / 业绩核算期 / 初次接触时间 / 预计AGV下线时间 /
预计进场时间）/ 车型信息（总车数（新加的）/ 车型1、车型2（这俩不变）/ 数量）/
调度软件（版本下增加一个版本号节点）。经确认：
  - 三组新节点都挂在「基础信息」下，排在最前（原来的客户信息等整体后移）；
  - 「车辆」改名「车型信息」—— 同一个节点，node_key（hardware.vehicle）与 id 都不动；
  - 台账同步随之一并填「项目编号」（它不再是定位列，见 info_node_ledger_sync_service）；
  - 文件导入提示词同步更新（车型分组改名 + 新增「总车数」规则）。

做法（幂等，可重复执行）：
  1. 按 node_key 找父节点；找不到（这个库没播过基础模板）就跳过那一类；
  2. 新增节点只在「同 node_key 的全局行不存在」时插入，id 用与播种同源的确定性
     UUIDv5（info_node_seed_service._seed_node_id），新库播种与老库迁移结果一致；
  3. 改名只改 node_name，且只在当前还叫「车辆」时改（管理员手工改过的不动）；
  4. 最后把这几个父节点下的兄弟重排成 10/20/30…：新节点插到约定位置、既有节点
     整体后移。只改数字不改相对顺序，前端看到的排序不变；库里的「车型3」
     （模板外自建的车型槽位）跟着一起排进去，不会被这轮重排挤丢。

downgrade：删掉本迁移新增的节点（连同各项目已填的值），名字改回「车辆」，重排回连续序号。
"""
from datetime import datetime
from typing import Sequence, Tuple, Union
import uuid

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = '6f2c8a1d9b47'
down_revision: Union[str, None] = '5b8e3f2a9c47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 「车辆」→「车型信息」：只改显示名
VEHICLE_KEY = 'hardware.vehicle'
VEHICLE_OLD_NAME = '车辆'
VEHICLE_NEW_NAME = '车型信息'

# 新增节点：(node_key, 节点名, 标题路径, 父 node_key, node_type)
# 「标题路径」只用来推 id —— 播种服务的 id 是按 default.yaml 的标题路径算的
# （_seed_node_id('/'.join(path))），这里必须同源，新库播种与老库迁移才会是同一批 id。
NEW_NODES: Tuple[Tuple[str, str, str, str, str], ...] = (
    ('base.project_code', '项目编号', '基础信息/项目编号', 'base', 'field'),
    ('base.order_no', '订单号', '基础信息/订单号', 'base', 'field'),
    ('base.time_summary', '时间信息汇总', '基础信息/时间信息汇总', 'base', 'group'),
    ('base.time_summary.created_at', '项目创建时间', '基础信息/时间信息汇总/项目创建时间', 'base.time_summary', 'field'),
    ('base.time_summary.settlement_period', '业绩核算期', '基础信息/时间信息汇总/业绩核算期', 'base.time_summary', 'field'),
    ('base.time_summary.first_contact', '初次接触时间', '基础信息/时间信息汇总/初次接触时间', 'base.time_summary', 'field'),
    ('base.time_summary.agv_offline_date', '预计AGV下线时间', '基础信息/时间信息汇总/预计AGV下线时间', 'base.time_summary', 'field'),
    ('base.time_summary.entry_date', '预计进场时间', '基础信息/时间信息汇总/预计进场时间', 'base.time_summary', 'field'),
    ('hardware.vehicle.total_count', '总车数', '硬件/车型信息/总车数', VEHICLE_KEY, 'field'),
    ('dispatch_software.version.number', '版本号', '调度软件/版本/版本号', 'dispatch_software.version', 'field'),
)

# 重排范围：新节点排在这几个父节点下的哪个位置（父 node_key → (排最前的, 排最后的)）
POSITIONS = {
    'base': (('base.project_code', 'base.order_no', 'base.time_summary'), ()),
    'base.time_summary': (('base.time_summary.created_at', 'base.time_summary.settlement_period',
                           'base.time_summary.first_contact', 'base.time_summary.agv_offline_date',
                           'base.time_summary.entry_date'), ()),
    VEHICLE_KEY: (('hardware.vehicle.total_count',), ()),
    'dispatch_software.version': ((), ('dispatch_software.version.number',)),
}


def _now_str() -> str:
    """与 delivery.py / info_node_seed_service 一致，时间戳存字符串。"""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _node_id(title_path: str) -> str:
    """标题路径 → 确定性 UUIDv5（与 info_node_seed_service._seed_node_id 同源）。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'ors://project-info-node/' + title_path))


def _global_node(conn, node_key: str):
    return conn.execute(text(
        "SELECT id, node_name, sort_order FROM project_info_node "
        "WHERE project_id IS NULL AND node_key = :key LIMIT 1"
    ), {'key': node_key}).fetchone()


def _children(conn, parent_id: str):
    """某个全局节点下的子节点，按现有序（sort_order, id）——重排要保住这个相对顺序。"""
    return conn.execute(text(
        "SELECT id, node_key, node_name, sort_order FROM project_info_node "
        "WHERE project_id IS NULL AND parent_id = :pid ORDER BY sort_order, id"
    ), {'pid': parent_id}).fetchall()


def _insert_node(conn, node_key: str, node_name: str, title_path: str,
                 parent_id: str, node_type: str) -> bool:
    """不存在同 node_key 的全局行才插入（幂等）。返回是否真的插了。"""
    if _global_node(conn, node_key) is not None:
        return False
    now = _now_str()
    conn.execute(text(
        "INSERT INTO project_info_node "
        "(id, project_id, parent_id, node_key, node_name, node_type, value_type, "
        " sort_order, required, allow_custom, config, status, created_by, created_at, updated_by, updated_at) "
        "VALUES (:id, NULL, :parent_id, :node_key, :node_name, :node_type, 'text', "
        " 0, 0, 1, NULL, 'active', NULL, :now, NULL, :now)"
    ), {
        'id': _node_id(title_path), 'parent_id': parent_id, 'node_key': node_key,
        'node_name': node_name, 'node_type': node_type, 'now': now,
    })
    return True


def _renumber(conn, parent_id: str, first_keys=(), last_keys=()) -> int:
    """父节点下的兄弟整体重排成 10/20/30…（first_keys 提前、last_keys 置后）。"""
    children = _children(conn, parent_id)
    by_key = {row.node_key: row for row in children}
    head = [by_key[key] for key in first_keys if key in by_key]
    tail = [by_key[key] for key in last_keys if key in by_key]
    picked = {row.id for row in head} | {row.id for row in tail}
    middle = [row for row in children if row.id not in picked]

    changed = 0
    for index, row in enumerate(head + middle + tail, start=1):
        order = index * 10
        if row.sort_order != order:
            conn.execute(text(
                "UPDATE project_info_node SET sort_order = :order WHERE id = :id"
            ), {'order': order, 'id': row.id})
            changed += 1
    return changed


def _upgrade(conn) -> None:
    """建新节点 + 改名 + 重排（拆成函数是为了能在回滚事务里试跑，见 tests）。"""
    inserted, skipped = 0, []
    for node_key, node_name, title_path, parent_key, node_type in NEW_NODES:
        parent = _global_node(conn, parent_key)
        if parent is None:      # 这个库没播过基础模板 → 整类跳过，别插出无父的孤儿
            skipped.append(node_key)
            continue
        if _insert_node(conn, node_key, node_name, title_path, parent.id, node_type):
            inserted += 1
    print(f"[base_new_nodes_and_vehicle_rename] 新增 {inserted} 个节点"
          + (f"，跳过 {len(skipped)} 个（父节点不存在：{', '.join(skipped)}）" if skipped else ""))

    # 「硬件 / 车辆」→「硬件 / 车型信息」（还叫「车辆」才改，管理员改过的名字不动）
    renamed = conn.execute(text(
        "UPDATE project_info_node SET node_name = :new, updated_at = :now "
        "WHERE project_id IS NULL AND node_key = :key AND node_name = :old"
    ), {'new': VEHICLE_NEW_NAME, 'old': VEHICLE_OLD_NAME, 'key': VEHICLE_KEY, 'now': _now_str()})
    print(f"[base_new_nodes_and_vehicle_rename] 「{VEHICLE_OLD_NAME}」→「{VEHICLE_NEW_NAME}」：{renamed.rowcount} 行")

    moved = 0
    for parent_key, (first_keys, last_keys) in POSITIONS.items():
        parent = _global_node(conn, parent_key)
        if parent is not None:
            moved += _renumber(conn, parent.id, first_keys, last_keys)
    print(f"[base_new_nodes_and_vehicle_rename] 同级重排：调整 {moved} 行 sort_order")


def upgrade() -> None:
    _upgrade(op.get_bind())


def downgrade() -> None:
    """回退到迁移前：删新节点（连同各项目已填的值）、名字改回「车辆」、序号重排回连续值。

    历史行（project_info_value_history）**保留**——它本来就是写入时快照，节点删了也该留着，
    应用层按 node_name 快照展示，不依赖节点行还在（见 ProjectInfoValueHistory 的说明）。
    """
    conn = op.get_bind()
    keys = [key for key, _, _, _, _ in NEW_NODES]
    for key in keys:
        node = _global_node(conn, key)
        if node is None:
            continue
        conn.execute(text("DELETE FROM project_info_value WHERE node_id = :id"), {'id': node.id})
        conn.execute(text("DELETE FROM project_info_node WHERE id = :id"), {'id': node.id})
    print(f"[base_new_nodes_and_vehicle_rename] downgrade：删除 {len(keys)} 个新增节点（不存在的跳过）")

    conn.execute(text(
        "UPDATE project_info_node SET node_name = :old, updated_at = :now "
        "WHERE project_id IS NULL AND node_key = :key AND node_name = :new"
    ), {'old': VEHICLE_OLD_NAME, 'new': VEHICLE_NEW_NAME, 'key': VEHICLE_KEY, 'now': _now_str()})

    for parent_key in POSITIONS:
        parent = _global_node(conn, parent_key)
        if parent is not None:
            _renumber(conn, parent.id)


__all__ = ['upgrade', 'downgrade', '_upgrade', 'NEW_NODES', 'POSITIONS', 'VEHICLE_KEY']
