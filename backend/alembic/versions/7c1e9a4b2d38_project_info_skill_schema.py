"""project_info_skill_schema

按 project-info-db-schema-SKILL 的模型整体重建项目信息库：把「模板」与「项目值」
拆开存，节点定义全局唯一，各项目只存自己的值。

旧结构（本次全部废弃）：
  project_info_node          每项目复制一整棵树的节点副本，结构里塞着值
  project_info_template      模板以邻接树 JSON 存在一行里，靠 template_node_id 锚点同步
  project_info_node_change   历史只留一句人话 detail，没有前后值
新结构：
  project_info_node          project_id IS NULL=全局定义；=A=A 项目的增补节点
  project_info_value         UNIQUE(project_id, node_id) 项目当前值，不预建空行
  project_info_value_history 唯一历史表，old/new 值 + 节点快照

⚠ 数据丢失：产品经理已确认旧表数据不要，直接 DROP 后按新结构重建并重新播种模板。
  现存项目已填的项目信息内容随之清空，不可回滚（upgrade 里没有数据搬迁）。
  project_info_node_mark（星标）**保留**，只是 node_id 的语义从「项目副本节点」
  改为「全局/增补节点身份」，表结构无需改动。

播种：全局模板节点（project_id IS NULL）按 app/config/project_templates/default.yaml
一次写入。node_key 用下面的 TITLE_KEY_MAP 按「标题路径」查表——路径缺失即中止迁移，
避免播出一批 key 拼错、之后再也对不上的节点。default.yaml 仍是唯一的结构来源，
key 只是给这套结构补上程序标识，故不写回 YAML（写回去等于又变成两套定义）。

Revision ID: 7c1e9a4b2d38
Revises: 1a2b3c4d5e6f
Create Date: 2026-09-17
"""
from typing import Sequence, Union

import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = '7c1e9a4b2d38'
down_revision: Union[str, None] = '1a2b3c4d5e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── 全局模板节点 key 表（标题路径 → node_key） ──────────
# 标题路径用 / 连接，与 default.yaml 的书写层级一一对应。
# key 命名：一级段取英文短名，其余段沿用其父的语义，字段名尽量直译（ssh.ip / wms ...）。
# 未列出的路径会让迁移直接失败——宁可当场炸，也不要播出半套 key。
TITLE_KEY_MAP = {
    # 基础信息
    '基础信息': 'base',
    '基础信息/客户信息': 'base.customer_info',
    '基础信息/订单信息': 'base.order',
    '基础信息/订单信息/ERP': 'base.order.erp',
    '基础信息/评审信息': 'base.review',
    '基础信息/项目区域/地点': 'base.location',
    '基础信息/项目区域/地点/区域选项': 'base.location.region',
    '基础信息/项目区域/地点/省份': 'base.location.province',
    '基础信息/项目区域/地点/地区': 'base.location.district',
    '基础信息/项目区域/地点/具体国家': 'base.location.country',
    '基础信息/项目类型': 'base.project_type',
    '基础信息/进厂要求': 'base.entry_requirement',
    '基础信息/进厂要求/着装': 'base.entry_requirement.dress_code',
    '基础信息/进厂要求/预约信息': 'base.entry_requirement.reservation',

    # 硬件
    '硬件': 'hardware',
    '硬件/车辆': 'hardware.vehicle',
    '硬件/车辆/车型1': 'hardware.vehicle.model_1',
    '硬件/车辆/车型1/数量': 'hardware.vehicle.model_1.quantity',
    '硬件/车辆/车型2': 'hardware.vehicle.model_2',
    '硬件/车辆/车型2/数量': 'hardware.vehicle.model_2.quantity',
    '硬件/载具类型': 'hardware.carrier_type',

    # 车端软件
    '车端软件': 'vehicle_software',
    '车端软件/控制器品牌': 'vehicle_software.controller_brand',
    '车端软件/软件版本': 'vehicle_software.version',
    '车端软件/数据同步方式': 'vehicle_software.data_sync_method',
    '车端软件/是否已与USP对接过': 'vehicle_software.usp_integrated',

    # 调度软件
    '调度软件': 'dispatch_software',
    '调度软件/版本': 'dispatch_software.version',
    '调度软件/版本/子模块': 'dispatch_software.version.submodule',
    '调度软件/版本/子模块/调度配置': 'dispatch_software.version.submodule.dispatch_config',
    '调度软件/版本/通用配置': 'dispatch_software.version.common_config',
    '调度软件/license': 'dispatch_software.license',
    '调度软件/license/到期时间': 'dispatch_software.license.expire_at',
    '调度软件/license/续期记录': 'dispatch_software.license.renewal_records',

    # 网络信息
    '网络信息': 'network',
    '网络信息/外网': 'network.internet',
    '网络信息/公网ip': 'network.public_ip',
    '网络信息/公网ip/服务器参数配置': 'network.public_ip.server_config',
    '网络信息/远程方式': 'network.remote',
    '网络信息/远程方式/SSH': 'network.remote.ssh',
    '网络信息/远程方式/SSH/IP': 'network.remote.ssh.ip',
    '网络信息/远程方式/SSH/端口': 'network.remote.ssh.port',
    '网络信息/远程方式/SSH/远程码': 'network.remote.ssh.code',
    '网络信息/远程方式/Todesk': 'network.remote.todesk',
    '网络信息/远程方式/Todesk/密码': 'network.remote.todesk.password',
    '网络信息/远程方式/AngDek': 'network.remote.angdek',
    '网络信息/远程方式/AngDek/远程码': 'network.remote.angdek.code',
    '网络信息/远程方式/AngDek/密码': 'network.remote.angdek.password',

    # 服务器部署
    '服务器部署': 'server_deploy',
    '服务器部署/中力服务器': 'server_deploy.zhongli',
    '服务器部署/中力服务器/是否与其他系统共用': 'server_deploy.zhongli.shared_with_others',
    '服务器部署/客户服务器': 'server_deploy.customer',
    '服务器部署/客户服务器/是否与其他系统共用': 'server_deploy.customer.shared_with_others',
    '服务器部署/云服务器': 'server_deploy.cloud',
    '服务器部署/云服务器/是否与其他系统共用': 'server_deploy.cloud.shared_with_others',

    # 环境
    '环境': 'environment',
    '环境/地图布局': 'environment.map_layout',
    '环境/地图布局/CAD源文件': 'environment.map_layout.cad',
    '环境/地图布局/CAD源文件/库位': 'environment.map_layout.cad.slots',
    '环境/地图布局/CAD源文件/库区形式/数量': 'environment.map_layout.cad.area_type_count',
    '环境/地图布局/AGV路线动线': 'environment.map_layout.agv_route',
    '环境/地图布局/通道与托盘间距尺寸': 'environment.map_layout.aisle_pallet_clearance',
    '环境/外设': 'environment.peripheral',
    '环境/外设/电梯': 'environment.peripheral.elevator',
    '环境/外设/电梯/厂家品牌(协议)': 'environment.peripheral.elevator.brand_protocol',
    '环境/外设/自动门': 'environment.peripheral.auto_door',
    '环境/外设/呼叫器': 'environment.peripheral.pager',
    '环境/外设/输送线/隔离线': 'environment.peripheral.conveyor',
    '环境/外设/红绿灯': 'environment.peripheral.traffic_light',
    '环境/外设/机械臂': 'environment.peripheral.robot_arm',
    '环境/外设/码垛机/盘盘机': 'environment.peripheral.palletizer',
    '环境/外设/缠绕机': 'environment.peripheral.wrapper',
    '环境/外设/光电': 'environment.peripheral.photoelectric',
    '环境/外设/无外设声明': 'environment.peripheral.none_declared',
    '环境/外设/其他(自定义)': 'environment.peripheral.other',

    # 业务系统
    '业务系统': 'business_system',
    '业务系统/系统': 'business_system.system',
    '业务系统/系统/DAS': 'business_system.system.das',
    '业务系统/系统/客户WMS': 'business_system.system.customer_wms',
    '业务系统/系统/客户MES/ERP': 'business_system.system.customer_mes_erp',
    '业务系统/系统/客户系统': 'business_system.system.customer_custom',
    '业务系统/系统/客户系统/其他上层系统': 'business_system.system.customer_custom.other_upper',
    '业务系统/系统/数字孪生': 'business_system.system.digital_twin',
    '业务系统/系统/数字孪生/接口协议': 'business_system.system.digital_twin.protocol',
    '业务系统/系统/数字孪生/接口标准': 'business_system.system.digital_twin.standard',
    '业务系统/系统/数字孪生/是否对接': 'business_system.system.digital_twin.integrated',
    '业务系统/系统/数字孪生/ip/url': 'business_system.system.digital_twin.ip_url',
    '业务系统/系统/PDA': 'business_system.system.pda',
    '业务系统/系统/平板': 'business_system.system.tablet',

    # 业务流程
    '业务流程': 'business_flow',
    '业务流程/搬运场景': 'business_flow.transport_scenario',
    '业务流程/搬运场景/搬运类型': 'business_flow.transport_scenario.type',
    '业务流程/搬运场景/装卸': 'business_flow.transport_scenario.loading',
    '业务流程/搬运场景/分拣': 'business_flow.transport_scenario.sorting',
    '业务流程/节拍': 'business_flow.takt',
    '业务流程/节拍/节拍': 'business_flow.takt.value',
    '业务流程/节拍/效率要求数值/无效率声明': 'business_flow.takt.efficiency_requirement',
    '业务流程/物料类型': 'business_flow.material_type',

    # 人员信息
    '人员信息': 'personnel',
    '人员信息/客户对象': 'personnel.customer_contact',
    '人员信息/实施': 'personnel.implementation',
    '人员信息/车端': 'personnel.vehicle_side',
    '人员信息/调度': 'personnel.dispatch',
    '人员信息/业务': 'personnel.business',
    '人员信息/项目经理': 'personnel.project_manager',
    '人员信息/销售': 'personnel.sales',
    '人员信息/售前': 'personnel.pre_sales',
    '人员信息/集成商': 'personnel.integrator',

    # 项目特性
    '项目特性': 'project_feature',
    '项目特性/风险点': 'project_feature.risk',
    '项目特性/注意事项': 'project_feature.attention',
    '项目特性/时间线': 'project_feature.timeline',
    '项目特性/时间线/大节点': 'project_feature.timeline.major_milestone',
    '项目特性/时间线/小节点': 'project_feature.timeline.minor_milestone',

    # 项目配置
    '项目配置': 'project_config',
    '项目配置/识别': 'project_config.recognition',

    # 项目定制
    '项目定制': 'project_customization',
    '项目定制/接口': 'project_customization.api',
    '项目定制/大屏': 'project_customization.dashboard',
    '项目定制/大屏/页面': 'project_customization.dashboard.page',
    '项目定制/功能': 'project_customization.feature',
}

# 旧值类型（content_type）→ 新值类型（value_type）。
# 旧前端只有 text/select/file/image 四种；新模型多了 number/date/person 等，
# 但存量没有能判定出这些类型的线索，一律按 text 起（管理员后续可在模板里改）。
VALUE_TYPE_MAP = {
    'text': 'text',
    'select': 'select',
    'file': 'attachment',
    'image': 'attachment',
}

# 允许在下面增补项目自定义子节点的节点（节点 key → True）。
# 来源：思维导图里标注了「对接权用户开放新增 / 支持新增」的节点。
ALLOW_CUSTOM_KEYS = {
    'base.project_type',
    'vehicle_software.controller_brand',
}

# 全局节点的「创建人」记为空：模板是系统播种的，不是某个管理员建的。
SEED_CREATED_BY = None


def _table_exists(table: str) -> bool:
    conn = op.get_bind()
    row = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :name"
        ),
        {"name": table},
    ).scalar()
    return bool(row)


def _load_seed_nodes():
    """读取 default.yaml 的 info_nodes，返回 [(path_tuple, node_dict), ...] 前序列表。"""
    import os
    import yaml

    here = os.path.dirname(os.path.abspath(__file__))
    # alembic/versions/ → backend/
    backend_root = os.path.dirname(os.path.dirname(here))
    yaml_path = os.path.join(backend_root, 'app', 'config', 'project_templates', 'default.yaml')
    with open(yaml_path, encoding='utf-8') as fh:
        data = yaml.safe_load(fh) or {}

    flat = []

    def walk(nodes, path):
        for node in nodes or []:
            current = path + (node.get('title', ''),)
            flat.append((current, node))
            walk(node.get('children'), current)

    walk(data.get('info_nodes'), ())
    return flat


def upgrade() -> None:
    conn = op.get_bind()

    # ── 1. 丢弃旧结构（数据不要） ──────────────────────
    # project_info_node_mark 不在其列：星标保留，表结构不变，只是 node_id 的语义变了。
    for table in ('project_info_value_history', 'project_info_value',
                  'project_info_node_change', 'project_info_template', 'project_info_node'):
        if _table_exists(table):
            op.drop_table(table)

    # ── 2. 建新表 ──────────────────────────────────────
    op.create_table(
        'project_info_node',
        sa.Column('id', sa.String(64), primary_key=True, comment='节点永久身份(UUID)，改名/挪位不换'),
        sa.Column('project_id', sa.String(64), nullable=True,
                  comment='NULL=全局模板节点；非 NULL=该项目专属的增补节点'),
        sa.Column('parent_id', sa.String(64), nullable=True, comment='父节点ID, NULL=根节点'),
        sa.Column('node_key', sa.String(191), nullable=False,
                  comment='程序用稳定标识(如 base.customer_info)，建立后不可改'),
        sa.Column('node_name', sa.String(255), nullable=False, comment='节点显示名'),
        sa.Column('node_type', sa.String(16), nullable=False, server_default='field',
                  comment='节点类型: root/group/field'),
        sa.Column('value_type', sa.String(32), nullable=False, server_default='text',
                  comment='值类型: text/number/boolean/date/select/multi_select/person/attachment/json'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0',
                  comment='同级排序(升序，建议留 10/20/30 间隔便于插入)'),
        sa.Column('required', sa.Boolean(), nullable=False, server_default=sa.text('0'),
                  comment='是否必填（前端信息完整性提示用）'),
        sa.Column('allow_custom', sa.Boolean(), nullable=False, server_default=sa.text('0'),
                  comment='是否允许在其下增补项目自定义子节点'),
        sa.Column('config', sa.JSON(), nullable=True, comment='节点配置: 下拉选项、单位、占位提示等'),
        sa.Column('status', sa.String(16), nullable=False, server_default='active',
                  comment='状态: active/disabled（停用保留历史与值，仅隐去）'),
        sa.Column('created_by', sa.String(64), nullable=True, comment='创建人登录名（全局节点为管理员）'),
        sa.Column('created_at', sa.String(30), nullable=False, comment='创建时间'),
        sa.Column('updated_by', sa.String(64), nullable=True, comment='最近修改人登录名'),
        sa.Column('updated_at', sa.String(30), nullable=False, comment='更新时间'),
    )
    op.create_index('idx_pin_scope', 'project_info_node', ['project_id', 'status'])
    op.create_index('idx_pin_parent_sort', 'project_info_node',
                    ['project_id', 'parent_id', 'sort_order'])
    # 全局作用域（project_id IS NULL）的 node_key 唯一性在 MySQL 里表达不了——
    # NULL 不参与唯一比较，这道索引只管「同项目内不重复」，全局唯一走 Service 层校验。
    op.create_index('uq_pin_project_key', 'project_info_node', ['project_id', 'node_key'],
                    unique=True)

    op.create_table(
        'project_info_value',
        sa.Column('id', sa.String(64), primary_key=True, comment='记录UUID'),
        sa.Column('project_id', sa.String(64), nullable=False, comment='值所属项目ID'),
        sa.Column('node_id', sa.String(64), nullable=False,
                  comment='对应的节点ID（全局节点或本项目增补节点）'),
        sa.Column('value_json', sa.JSON(), nullable=True,
                  comment='节点值(原生JSON: 字符串/数字/数组/对象)'),
        sa.Column('created_at', sa.String(30), nullable=False, comment='创建时间'),
        sa.Column('updated_at', sa.String(30), nullable=False, comment='更新时间'),
        sa.Column('updated_by', sa.String(64), nullable=True, comment='最近修改人登录名'),
    )
    op.create_index('uq_piv_project_node', 'project_info_value', ['project_id', 'node_id'],
                    unique=True)
    op.create_index('idx_piv_node', 'project_info_value', ['node_id'])

    op.create_table(
        'project_info_value_history',
        sa.Column('id', sa.String(64), primary_key=True, comment='记录UUID（时间有序，可当水位比较）'),
        sa.Column('project_id', sa.String(64), nullable=False,
                  comment='所属项目ID（历史按项目隔离，必填）'),
        sa.Column('node_id', sa.String(64), nullable=True,
                  comment='被操作的节点ID；整树级操作为 NULL'),
        sa.Column('parent_id', sa.String(64), nullable=True,
                  comment='上级节点ID（删除记录据此在父节点历史里展示）'),
        sa.Column('node_key', sa.String(191), nullable=False, server_default='',
                  comment='写入时的节点标识快照'),
        sa.Column('node_name', sa.String(255), nullable=False, server_default='',
                  comment='写入时的节点名称快照'),
        sa.Column('node_type', sa.String(16), nullable=True, comment='写入时的节点类型快照'),
        sa.Column('old_value', sa.JSON(), nullable=True, comment='变更前的值（原生JSON）'),
        sa.Column('new_value', sa.JSON(), nullable=True, comment='变更后的值（原生JSON）'),
        sa.Column('operation_type', sa.String(16), nullable=False,
                  comment='操作类型: create/update/delete/node_create/node_move/node_rename'),
        sa.Column('changed_by', sa.String(64), nullable=True, comment='操作人登录名（识别不到时为 NULL）'),
        sa.Column('changed_by_name', sa.String(64), nullable=True, comment='操作人显示名'),
        sa.Column('change_reason', sa.Text(), nullable=True, comment='变更原因（预留，当前多数为空）'),
        sa.Column('detail', sa.Text(), nullable=True,
                  comment='具体变动的人话描述（服务端拼好，前端直接展示）'),
        sa.Column('changed_at', sa.String(30), nullable=False, comment='操作时间'),
    )
    op.create_index('idx_pivh_project_time', 'project_info_value_history',
                    ['project_id', 'changed_at'])
    op.create_index('idx_pivh_project_node_time', 'project_info_value_history',
                    ['project_id', 'node_id', 'changed_at'])
    op.create_index('idx_pivh_parent', 'project_info_value_history',
                    ['project_id', 'parent_id'])

    # ── 3. 播种全局模板节点 ────────────────────────────
    flat = _load_seed_nodes()
    missing = [p for p, _ in flat if '/'.join(x for x in p) not in TITLE_KEY_MAP]
    if missing:
        raise RuntimeError(
            'default.yaml 里有节点没在 TITLE_KEY_MAP 里登记 node_key：'
            + '; '.join('/'.join(p) for p in missing)
        )

    now = _now_str()
    id_by_path = {}
    rows = []
    sibling_seq = {}
    for path, node in flat:
        path_key = '/'.join(path)
        node_id = _seed_node_id(path_key)
        id_by_path[path] = node_id
        children = node.get('children') or []
        content_type = node.get('content_type') or 'text'
        value_type = VALUE_TYPE_MAP.get(content_type, 'text')
        node_key = TITLE_KEY_MAP[path_key]

        if len(path) == 1:
            node_type = 'root'
        elif children:
            node_type = 'group'
        else:
            node_type = 'field'

        config = None
        if value_type == 'select' and node.get('options'):
            # bulk_insert 走的是 executemany 的字符串插值，JSON 列这里必须给已序列化的
            # 字符串——直接塞 dict 会在 pymysql 的 escape 阶段抛
            # TypeError: dict can not be used as parameter。
            config = json.dumps({'options': [{'value': opt, 'label': opt} for opt in node['options']]},
                                ensure_ascii=False)

        # 排序取「同父下的第几个」，每个父各自从 10 起算——与 default.yaml 的
        # 书写顺序一致，且留出间隔（写入端插入新节点时取中位即可，不必整排重编）。
        parent_path = path[:-1]
        sibling_seq[parent_path] = sibling_seq.get(parent_path, 0) + 1

        rows.append({
            'id': node_id,
            'project_id': None,
            'parent_id': id_by_path.get(parent_path),
            'node_key': node_key,
            'node_name': node.get('title', ''),
            'node_type': node_type,
            'value_type': value_type,
            'sort_order': sibling_seq[parent_path] * 10,
            'required': False,
            'allow_custom': node_key in ALLOW_CUSTOM_KEYS,
            'config': config,
            'status': 'active',
            'created_by': SEED_CREATED_BY,
            'created_at': now,
            'updated_by': None,
            'updated_at': now,
        })

    table = sa.table(
        'project_info_node',
        *(sa.column(name) for name in (
            'id', 'project_id', 'parent_id', 'node_key', 'node_name', 'node_type',
            'value_type', 'sort_order', 'required', 'allow_custom', 'config',
            'status', 'created_by', 'created_at', 'updated_by', 'updated_at',
        )),
    )
    op.bulk_insert(table, rows)

    # 全局层次里的 node_key 必须唯一（DB 索引管不到 project_id IS NULL 这一半）
    dupes = conn.execute(text(
        "SELECT node_key FROM project_info_node WHERE project_id IS NULL "
        "GROUP BY node_key HAVING COUNT(*) > 1"
    )).fetchall()
    if dupes:
        raise RuntimeError('播种的全局节点 node_key 有重复：'
                           + ', '.join(row[0] for row in dupes))


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _seed_node_id(path_key: str) -> str:
    """播种节点的 id：用标题路径做确定性 UUIDv5，重复执行迁移得到同一批 id。

    新结构下 id 是节点永久身份，随机生成也能用；选确定性生成是为了让「同一份
    default.yaml 播出的树在任何环境下 id 一致」，排查问题时能直接对得上。
    """
    import uuid
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'ors://project-info-node/' + path_key))


def downgrade() -> None:
    """回退到旧结构（只重建空表骨架，旧数据与旧模板无法恢复）。

    ⚠ 这不是真正的回滚：旧表数据在 upgrade 里已被丢弃，这里只能把四张空表
    按旧定义建回来（含 project_info_template / project_info_node_change），
    让代码层面能降级运行。已填的项目信息内容无法找回。
    """
    for table in ('project_info_value_history', 'project_info_value'):
        if _table_exists(table):
            op.drop_table(table)

    if _table_exists('project_info_node'):
        op.drop_table('project_info_node')

    op.create_table(
        'project_info_node',
        sa.Column('id', sa.String(64), primary_key=True, comment='节点UUID(客户端生成)'),
        sa.Column('project_id', sa.String(64), nullable=False, comment='所属项目ID'),
        sa.Column('parent_id', sa.String(64), nullable=True, comment='父节点ID, NULL=根节点'),
        sa.Column('title', sa.String(255), nullable=False, comment='节点标题'),
        sa.Column('content_type', sa.String(32), nullable=False, server_default='text'),
        sa.Column('value', sa.Text(), nullable=True, comment='节点值'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('template_node_id', sa.String(64), nullable=True),
        sa.Column('created_at', sa.String(30), nullable=False),
        sa.Column('updated_at', sa.String(30), nullable=False),
    )
    op.create_index('idx_pn_project', 'project_info_node', ['project_id'])
    op.create_index('idx_pn_parent', 'project_info_node', ['parent_id'])
    op.create_index('idx_pn_project_parent_sort', 'project_info_node',
                    ['project_id', 'parent_id', 'sort_order'])

    op.create_table(
        'project_info_template',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False, server_default='项目详情模板'),
        sa.Column('nodes', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.String(30), nullable=True),
        sa.Column('updated_by', sa.String(128), nullable=True),
    )

    op.create_table(
        'project_info_node_change',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('project_id', sa.String(64), nullable=False),
        sa.Column('node_id', sa.String(64), nullable=True),
        sa.Column('parent_id', sa.String(64), nullable=True),
        sa.Column('node_title', sa.String(255), nullable=False, server_default=''),
        sa.Column('action', sa.String(16), nullable=False),
        sa.Column('operator', sa.String(64), nullable=True),
        sa.Column('operator_name', sa.String(64), nullable=True),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('created_at', sa.String(30), nullable=False),
    )
    op.create_index('idx_pnc_project_time', 'project_info_node_change',
                    ['project_id', 'created_at'])
    op.create_index('idx_pnc_node', 'project_info_node_change', ['project_id', 'node_id'])
    op.create_index('idx_pnc_parent', 'project_info_node_change', ['project_id', 'parent_id'])
