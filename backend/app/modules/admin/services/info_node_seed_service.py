"""全局项目信息节点的启动播种（空库自动补齐）。

背景：全局节点定义（project_info_node 里 project_id IS NULL 的那批行）原本只由
alembic 迁移 7c1e9a4b2d38 从 default.yaml 播一次。测试环境是另一套数据库，不一定
跑过本机的迁移历史，库里没有这些节点，整个「项目信息」就是一棵空树——所以后端启动
时补一道：**一个全局节点都没有**才播，播完即止。已有全局节点的库（开发机、已上线
的库）原样跳过，不改、不删、不覆盖。

结构来源仍是 default.yaml（唯一来源）；TITLE_KEY_MAP 从迁移里原样搬来，此后以本
文件为准（迁移是历史，不再维护）。id 沿用迁移的确定性 UUIDv5（标题路径 → id），
同一份 default.yaml 在任何环境播出同一批 id，排查问题能直接对上。

播种形态必须与迁移链的终态一致，否则新库会缺后续迁移的效果：
  - 车型1/车型2 是下拉，选项 = 代码里的车型目录（VEHICLE_MODEL_CODES，不在 yaml 里抄）
    —— 迁移 9d2f4a6b8c01 / 4a7c2e9d1b53；
  - 所有节点 allow_custom=True —— 迁移 5b8e3f2a9c47。

旧结构的 project_info_node 表（列不同）会让查询直接报错、由调用方记日志跳过——
宁可整个不播，也不要往旧表里塞半套数据。
"""
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Tuple

from sqlalchemy import null

from app.core.db import SessionLocal
from app.models.delivery import ProjectInfoNode

logger = logging.getLogger(__name__)


# ── 全局模板节点 key 表（标题路径 → node_key） ──────────────
# 与迁移 7c1e9a4b2d38_project_info_skill_schema.py 里的那份逐字一致（搬过来用），
# 迁移已应用、那份是历史；之后新增节点只维护这一份。
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

# 内容形式 → 值类型。yaml 里只有 text（默认）/ select / file / image 四种。
VALUE_TYPE_MAP = {
    'text': 'text',
    'select': 'select',
    'file': 'attachment',
    'image': 'attachment',
}

# 迁移 9d2f4a6b8c01 + 4a7c2e9d1b53 把这两行从文字改成了下拉、选项铺车型目录。
# default.yaml 有意不抄目录（见该文件里车辆段的注释），这里按同样口径现挂。
_VEHICLE_MODEL_KEYS = ('hardware.vehicle.model_1', 'hardware.vehicle.model_2')


def _now_str() -> str:
    """与 delivery.py / info_node_service 一致，用字符串存时间戳。"""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _seed_node_id(path_key: str) -> str:
    """标题路径 → 确定性 UUIDv5（与迁移同源）：同一份 yaml 在任何环境同一批 id。"""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'ors://project-info-node/' + path_key))


def _flatten_nodes(nodes: List[Dict],
                   path: Tuple[str, ...] = ()) -> List[Tuple[Tuple[str, ...], Dict]]:
    """yaml 节点树 → 前序 [(标题路径, 节点), ...]（父一定排在子前面）。"""
    flat: List[Tuple[Tuple[str, ...], Dict]] = []
    for node in nodes or []:
        current = path + (str(node.get('title', '')),)
        flat.append((current, node))
        flat.extend(_flatten_nodes(node.get('children') or [], current))
    return flat


def _vehicle_model_codes() -> List[str]:
    """车型型号清单（后端唯一一份）。延迟导入：避免启动期把导入链整个拉起来。"""
    from app.modules.admin.services.info_node_import_service import VEHICLE_MODEL_CODES

    return list(VEHICLE_MODEL_CODES)


def build_seed_rows() -> List[ProjectInfoNode]:
    """按 default.yaml 生成全局节点行（不落库，播种与测试共用）。"""
    from app.modules.admin.services.project_service import get_info_nodes_template_from_yaml

    flat = _flatten_nodes(get_info_nodes_template_from_yaml())
    if not flat:
        return []

    missing = ['/'.join(path) for path, _ in flat if '/'.join(path) not in TITLE_KEY_MAP]
    if missing:
        # 与迁移同口径：宁可当场炸，也不要播出一批 key 拼错的节点
        raise RuntimeError('default.yaml 里有节点没在 TITLE_KEY_MAP 里登记 node_key：'
                           + '; '.join(missing))

    now = _now_str()
    id_by_path: Dict[Tuple[str, ...], str] = {}
    sibling_seq: Dict[Tuple[str, ...], int] = {}
    rows: List[ProjectInfoNode] = []

    for path, node in flat:
        path_key = '/'.join(path)
        node_id = _seed_node_id(path_key)
        id_by_path[path] = node_id
        children = node.get('children') or []
        node_key = TITLE_KEY_MAP[path_key]
        value_type = VALUE_TYPE_MAP.get(node.get('content_type') or 'text', 'text')
        if node_key in _VEHICLE_MODEL_KEYS:
            # yaml 里车型1/车型2 还是普通文字节点（见该文件车辆段的注释），
            # 下拉是迁移链的终态——这里补上，新库才不会少这一改。
            value_type = 'select'

        # 没有配置的节点写 SQL NULL：JSON 列传 None 会落成 JSON 'null'，与迁移播出的行对不上
        config = null()
        if value_type == 'select':
            options = _vehicle_model_codes() if node_key in _VEHICLE_MODEL_KEYS \
                else (node.get('options') or [])
            if options:
                config = {'options': [{'value': opt, 'label': opt} for opt in options]}

        # 排序取「同父下的第几个」，每个父各自从 10 起算——与 default.yaml 的书写
        # 顺序一致，且留出间隔（写入端插入新节点时取中位即可，不必整排重编）。
        parent_path = path[:-1]
        sibling_seq[parent_path] = sibling_seq.get(parent_path, 0) + 1

        rows.append(ProjectInfoNode(
            id=node_id,
            project_id=None,
            parent_id=id_by_path.get(parent_path),
            node_key=node_key,
            node_name=str(node.get('title', '')),
            node_type='root' if len(path) == 1 else ('group' if children else 'field'),
            value_type=value_type,
            sort_order=sibling_seq[parent_path] * 10,
            required=False,
            allow_custom=True,   # 迁移 5b8e3f2a9c47 的终态：所有节点都允许增补
            config=config,
            status='active',
            created_by=None,     # 系统播种，不是某个管理员建的
            created_at=now,
            updated_by=None,
            updated_at=now,
        ))
    return rows


def ensure_global_info_nodes() -> int:
    """空库时播种全局模板节点，返回本次写入的节点数（已有则 0）。

    幂等：只看「有没有全局节点」这一个条件，重复启动不会重复播、也不会动已存在的
    节点——管理员在「详情模板」里改过、停用过的节点必须原样留着。
    """
    db = SessionLocal()
    try:
        existing = (db.query(ProjectInfoNode.id)
                    .filter(ProjectInfoNode.project_id.is_(None))
                    .count())
        if existing:
            logger.info('[项目信息节点] 已有 %s 个全局节点，跳过播种', existing)
            return 0

        rows = build_seed_rows()
        if not rows:
            logger.warning('[项目信息节点] default.yaml 里没有 info_nodes，跳过播种')
            return 0

        db.add_all(rows)
        db.commit()
        logger.info('[项目信息节点] 空库播种完成：写入 %s 个全局节点', len(rows))
        return len(rows)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
