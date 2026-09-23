"""DAS 交付管理 ORM 模型。

原定义于 `app/modules/das/models/models.py`，现迁入此处作为唯一定义点（MIGRATION.md 阶段 1）。
`das/models/models.py` 改为从本模块再导出（其中本文件的 `ProjectDelivery` 以 `Project` 名再导出，
使 `das/services/*` 的旧导入保持不变）。

注意：DAS 的项目类在此更名为 `ProjectDelivery`，以与身份底座的 `app.models.identity.Project`
共存于同一 `app.models` 命名空间；**物理表名 `project` 保持不变**（双表合并留待 Wave 2）。

含 9 张表：realtime_data / history_data / collection_data / project / risk /
project_daily_report / project_license / project_transport_efficiency /
project_transport_efficiency_robot
"""
from sqlalchemy import Column, Integer, BigInteger, Boolean, String, Text, Float, Index, JSON

from app.models.base import Base

# 项目承接状态（对应企业微信项目表「是否承接」列的原值）。
# 「否」的项目不入库（见 integrations/sources/wecom/adapter.py 同步过滤），
# 故库内只有这两种取值；除仪表盘月柱图外，所有项目列表默认只取「是」。
UNDERTAKE_YES = '是'
UNDERTAKE_PENDING = '待定'

# 项目软删除标记值：删除项目时不物理删除 project 记录，而是把 status 置为此值，
# 保留记录用于后续创建项目时按编号/名称去重（已删除记录仍占用编号与名称）。
# 所有项目列表/详情查询均应排除此状态。
PROJECT_DELETED = '已删除'


class RealtimeData(Base):
    __tablename__ = 'realtime_data'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project = Column(String(50), nullable=False)
    indicator = Column(String(100), nullable=False)
    data = Column(Text, nullable=False)
    collection_time = Column(String(50), nullable=False)
    record_time = Column(String(50), nullable=False)

    __table_args__ = (
        Index('idx_project_indicator', 'project', 'indicator'),
        Index('idx_collection_time', 'collection_time'),
    )

    def __repr__(self):
        return f"<RealtimeData(id={self.id}, project='{self.project}', indicator='{self.indicator}')>"


class HistoryData(Base):
    __tablename__ = 'history_data'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project = Column(String(50), nullable=False)
    indicator = Column(String(100), nullable=False)
    data = Column(Text, nullable=False)
    collection_time = Column(String(50), nullable=False)
    record_time = Column(String(50), nullable=False)
    time_str = Column(String(100), nullable=False)
    start_time = Column(BigInteger, nullable=False)
    end_time = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index('idx_hist_project_indicator', 'project', 'indicator'),
        Index('idx_hist_collection_time', 'collection_time'),
        Index('idx_hist_unique_key', 'project', 'indicator', 'start_time', 'end_time'),
    )

    def __repr__(self):
        return f"<HistoryData(id={self.id}, project='{self.project}', indicator='{self.indicator}', start_time='{self.start_time}', end_time='{self.end_time}')>"


class CollectionData(Base):
    __tablename__ = 'collection_data'

    id = Column(Integer, primary_key=True)
    project = Column(String(50), nullable=False)
    indicator = Column(String(100), nullable=False)
    start_time_int = Column(BigInteger, nullable=False, comment='数据采集开始时间戳用于查询')
    end_time_int = Column(BigInteger, nullable=False, comment='数据采集结束时间戳用于查询')
    data = Column(Text, nullable=False)
    collection_time = Column(String(50), nullable=False)
    record_time = Column(String(50), nullable=False)
    time_str = Column(String(100), nullable=False)

    __table_args__ = (
        Index('idx_coll_unique_key', 'project', 'indicator', 'start_time_int', 'end_time_int'),
        Index('idx_coll_time', 'start_time_int'),
    )

    def __repr__(self):
        return f"<CollectionData(id={self.id}, project='{self.project}', indicator='{self.indicator}', start_time_int='{self.start_time_int}', end_time_int='{self.end_time_int}')>"


class Project(Base):
    __tablename__ = 'project'

    id = Column(String(64), primary_key=True, comment='项目ID/代码，与code一致')
    code = Column(String(64), unique=True, nullable=False, comment='项目代码')
    name = Column(String(128), nullable=False, comment='项目名称')

    system_id = Column(String(50), nullable=True, comment='系统ID')
    description = Column(Text, nullable=True, comment='项目描述')
    contact_person = Column(String(50), nullable=True, comment='对接人')
    contact_person_id = Column(String(64), nullable=True, comment='对接人ID（与 users.id 同长度）')
    project_contact = Column(String(50), nullable=True, comment='对接人')
    status = Column(String(20), nullable=False, default='active', comment='状态')
    expected_trend = Column(String(20), nullable=True, comment='预计走向')
    issues = Column(Integer, nullable=False, default=0, comment='问题数')
    risks = Column(Integer, nullable=False, default=0, comment='风险数')
    personnel_plan = Column(String(50), nullable=True, comment='人员计划')
    risk_list = Column(Text, nullable=True, comment='风险清单')
    deployment_date = Column(String(20), nullable=True, comment='部署时间')
    deployment_version = Column(String(50), nullable=True, comment='部署版本')
    recent_delivery_date = Column(String(20), nullable=True, comment='近期交付时间')
    recent_delivery_content = Column(Text, nullable=True, comment='近期交付内容')
    final_delivery_date = Column(String(20), nullable=True, comment='最终交付时间')
    project_summary = Column(Text, nullable=True, comment='项目总结')
    task_execution_status = Column(String(50), nullable=True, comment='任务执行情况')
    field_links = Column(Text, nullable=True, comment='字段链接(JSON格式)')
    category_basis = Column(String(20), nullable=False, default='重要紧急', comment='分类依据')

    project_type = Column(String(20), nullable=True, comment='项目类型（企业微信项目类型字段原值）')
    stage_notes = Column(Text, nullable=True, comment='生命周期各阶段补充说明(JSON格式，键为阶段名)')
    risk_carrying_type = Column(String(20), nullable=True, comment='风险承接类型')
    special_attention = Column(Text, nullable=True, comment='特别关注说明')
    risk_task_description = Column(Text, nullable=True, comment='风险和任务描述')
    management_strategy = Column(Text, nullable=True, comment='项目管理策略')
    project_documents = Column(Text, nullable=True, comment='项目文档(JSON格式，[{name,resource_id,url}])')
    sales = Column(String(50), nullable=True, comment='销售')
    pre_sales = Column(String(50), nullable=True, comment='售前')
    project_manager = Column(String(50), nullable=True, comment='项目经理')
    project_manager_id = Column(String(64), nullable=True, comment='项目经理ID（与 users.id 同长度，用于关联角色）')
    field_engineer = Column(String(50), nullable=True, comment='实施工程师')

    internal_code = Column(String(50), nullable=True, comment='内部编号')
    project_region = Column(String(30), nullable=True, comment='项目区域/地点')
    total_vehicle_count = Column(Integer, nullable=True, comment='总车数')
    controller_vendor = Column(String(30), nullable=True, comment='控制器选择')
    system_integration = Column(Text, nullable=True, comment='系统/外设对接(JSON数组)')
    server_deployment_status = Column(String(30), nullable=True, comment='服务器部署')
    settlement_period = Column(String(20), nullable=True, comment='业绩核算期（手工填写，常见YYYYMM如202608，兼容YYYY-MM）')
    undertake_status = Column(
        String(10), nullable=False, default=UNDERTAKE_YES, server_default=UNDERTAKE_YES,
        comment='是否承接（是/待定；「否」不入库）',
    )

    # 项目扩展信息：承接「日益增长且丰富变化」的专属字段，支持递归嵌套字典/数组
    # （如 project.robots[].name、project.network.vlan）。稳定、需查询/统计/独立
    # 管理的字段应提升为主表列或拆子表，不应塞进 ext_info。
    ext_info = Column(JSON, nullable=True, comment='项目扩展信息(递归嵌套 JSON)')
    # 乐观锁版本号：update_project 校验客户端带回的 version，不一致返回 409，
    # 防止 ext_info 整文档读改写模式下多人同时编辑互相覆盖。
    version = Column(Integer, nullable=False, default=1, server_default='1', comment='乐观锁版本号')

    __table_args__ = (
        Index('idx_project_code', 'code', unique=True),
        Index('idx_project_status', 'status'),
        Index('idx_project_settlement_period', 'settlement_period'),
        Index('idx_project_undertake_status', 'undertake_status'),
    )

    def __repr__(self):
        return f"<Project(code='{self.code}', name='{self.name}')>"


class Risk(Base):
    __tablename__ = 'risk'

    id = Column(Integer, primary_key=True, autoincrement=True)
    risk_code = Column(String(50), nullable=False, unique=True, comment='风险代码')
    project_code = Column(String(50), nullable=False, comment='项目代码')
    project_name = Column(String(100), nullable=False, comment='项目名称')
    risk_category = Column(String(50), nullable=False, comment='风险分类')
    custom_category = Column(String(50), nullable=True, comment='自定义分类')
    description = Column(String(1000), nullable=False, comment='风险描述')
    risk_level = Column(String(20), nullable=False, comment='风险等级')
    response_measure = Column(String(1000), nullable=True, comment='应对措施')
    progress = Column(String(100), nullable=True, comment='进度')
    responsible_person = Column(String(50), nullable=False, comment='负责人')
    responsible_person_id = Column(String(20), nullable=False, comment='负责人ID')
    status = Column(String(20), nullable=False, comment='状态')
    discovery_time = Column(String(20), nullable=False, comment='发现时间')
    close_time = Column(String(30), nullable=True, comment='关闭时间')
    created_at = Column(String(30), nullable=False, comment='创建时间')
    updated_at = Column(String(30), nullable=False, comment='更新时间')

    __table_args__ = (
        Index('idx_risk_project', 'project_code', 'project_name'),
        Index('idx_risk_status', 'status'),
        Index('idx_risk_discovery_time', 'discovery_time'),
    )

    def __repr__(self):
        return f"<Risk(id={self.id}, project_code='{self.project_code}', description='{self.description[:20]}...')>"


class ProjectDailyReport(Base):
    __tablename__ = 'project_daily_report'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_code = Column(String(50), nullable=False, comment='项目代码')
    report_date = Column(String(20), nullable=False, comment='日报日期')
    report_content = Column(Text, nullable=False, comment='日报内容(JSON格式)')
    reporter = Column(String(50), nullable=False, comment='报告人')
    reporter_id = Column(String(20), nullable=False, comment='报告人ID')
    created_at = Column(String(30), nullable=False, comment='创建时间')
    updated_at = Column(String(30), nullable=True, comment='更新时间')

    __table_args__ = (
        Index('idx_report_project_code', 'project_code'),
        Index('idx_report_date', 'report_date'),
        Index('idx_report_unique', 'project_code', 'report_date', unique=True),
    )

    def __repr__(self):
        return f"<ProjectDailyReport(id={self.id}, project_code='{self.project_code}', report_date='{self.report_date}')>"


class ProjectLicense(Base):
    __tablename__ = 'project_license'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_code = Column(String(50), nullable=False, comment='项目代码')
    machine_code = Column(String(200), nullable=True, comment='机器码/MAC地址')
    apply_time = Column(String(30), nullable=False, comment='申请时间')
    expire_time = Column(String(30), nullable=False, comment='过期时间')
    license_code = Column(Text, nullable=False, comment='授权码')
    applicant = Column(String(50), nullable=False, comment='申请人')
    applicant_id = Column(String(20), nullable=False, comment='申请人ID')
    max_vehicles = Column(Integer, nullable=True, comment='允许最大车数，为空表示不限制')
    created_at = Column(String(30), nullable=False, comment='创建时间')

    __table_args__ = (
        Index('idx_license_project_code', 'project_code'),
        Index('idx_license_apply_time', 'apply_time'),
        Index('idx_license_expire_time', 'expire_time'),
    )

    def __repr__(self):
        return f"<ProjectLicense(id={self.id}, project_code='{self.project_code}', license_code='{self.license_code}')>"


class ProjectTransportEfficiency(Base):
    __tablename__ = 'project_transport_efficiency'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_code = Column(String(50), nullable=False, comment='项目代码')
    report_date = Column(String(20), nullable=False, comment='数据日期')
    total_tasks = Column(Integer, nullable=True, comment='总任务数')
    carry_task_count = Column(Integer, nullable=True, comment='搬运任务数量')
    effective_work_hours = Column(Float, nullable=True, comment='有效工作时长(小时)')
    fault_hours = Column(Float, nullable=True, comment='机器人故障时长(小时)')
    idle_hours = Column(Float, nullable=True, comment='空闲无任务时间(小时)')
    avg_error_count = Column(Float, nullable=True, comment='平均错误次数')
    avg_fault_duration_minutes = Column(Float, nullable=True, comment='平均单次故障时间(分钟)')
    avg_carry_duration_minutes = Column(Float, nullable=True, comment='平均单次搬运任务时间(分钟)')
    avg_manual_switch_count = Column(Float, nullable=True, comment='平均切手动次数')
    manual_intervention_rate = Column(Float, nullable=True, comment='人工干预率(0-1小数)')
    created_at = Column(String(30), nullable=False, comment='创建时间')
    updated_at = Column(String(30), nullable=True, comment='更新时间')

    __table_args__ = (
        Index('idx_te_project_date', 'project_code', 'report_date', unique=True),
    )

    def __repr__(self):
        return f"<ProjectTransportEfficiency(id={self.id}, project_code='{self.project_code}', report_date='{self.report_date}')>"


class ProjectTransportEfficiencyRobot(Base):
    __tablename__ = 'project_transport_efficiency_robot'

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_code = Column(String(50), nullable=False, comment='项目代码')
    report_date = Column(String(20), nullable=False, comment='数据日期')
    robot_model = Column(String(50), nullable=False, comment='AGV机器人型号')
    carry_task_total = Column(Integer, nullable=True, comment='搬运任务总数(个)')
    effective_work_hours = Column(Float, nullable=True, comment='有效工作时长(h)')
    effective_efficiency = Column(Float, nullable=True, comment='有效搬运效率(小时/个)')
    fault_hours = Column(Float, nullable=True, comment='机器人故障时间(小时)')
    idle_hours = Column(Float, nullable=True, comment='无工作时间(小时)')
    avg_fault_duration_minutes = Column(Float, nullable=True, comment='平均单次故障(分钟)')
    avg_carry_duration_minutes = Column(Float, nullable=True, comment='平均单次搬运时间(分钟)')
    created_at = Column(String(30), nullable=False, comment='创建时间')

    __table_args__ = (
        Index('idx_ter_project_date_model', 'project_code', 'report_date', 'robot_model', unique=True),
    )

    def __repr__(self):
        return f"<ProjectTransportEfficiencyRobot(id={self.id}, project_code='{self.project_code}', report_date='{self.report_date}', robot_model='{self.robot_model}')>"


# 项目信息树「节点类型」与「值类型」枚举（与 SKILL 第 5.6 / 5.7 节一致）。
# 存字符串而非数据库 ENUM：后续加值类型不需要改表结构，校验放 Service 层。
PROJECT_INFO_NODE_TYPES = ('root', 'group', 'field')
PROJECT_INFO_VALUE_TYPES = (
    'text', 'number', 'boolean', 'date', 'select',
    'multi_select', 'person', 'attachment', 'json',
)
# 节点启停：停用（disabled）保留历史与项目值，仅从树查询里隐去。
PROJECT_INFO_NODE_ACTIVE = 'active'
PROJECT_INFO_NODE_DISABLED = 'disabled'


class ProjectInfoNode(Base):
    """项目信息树节点定义（邻接表，一个节点的「字段定义」只存一行）。

    **双用途**（见 SKILL 第 3 节）：
      project_id IS NULL → 全局模板节点，所有项目共用同一份定义；
      project_id = A      → 项目 A 的「增补」自定义节点，仅 A 可见。

    节点只描述结构（叫什么、什么类型、能不能加子节点），**不存值**——
    项目的实际数据一律放 project_info_value，靠 (project_id, node_id) 关联。
    同一个全局节点因此天然对应多个项目各自的值，不再像旧结构那样为每个项目
    复制一整棵树。

    id 是节点的永久身份，改名/改类型/挪位置都不换 id，历史与项目值的关联不失效。
    node_key 是给程序看的稳定标识（如 base.customer_info），node_name 只用于展示——
    改 node_name 不影响任何程序逻辑；node_key 建立后不允许修改。

    排序只认 sort_order（同 parent_id 下升序），与 id 大小无关。
    删除一律用 status='disabled' 软删（见 SKILL 第 5.12 节）：
    硬删会让历史记录与项目值失去挂靠点。
    """
    __tablename__ = 'project_info_node'

    id = Column(String(64), primary_key=True, comment='节点永久身份(UUID)，改名/挪位不换')
    project_id = Column(String(64), nullable=True, comment='NULL=全局模板节点；非 NULL=该项目专属的增补节点')
    parent_id = Column(String(64), nullable=True, comment='父节点ID, NULL=根节点')
    node_key = Column(String(191), nullable=False, comment='程序用稳定标识(如 base.customer_info)，建立后不可改')
    node_name = Column(String(255), nullable=False, comment='节点显示名')
    node_type = Column(String(16), nullable=False, default='field', comment='节点类型: root/group/field')
    value_type = Column(String(32), nullable=False, default='text', comment='值类型: text/number/boolean/date/select/multi_select/person/attachment/json')
    sort_order = Column(Integer, nullable=False, default=0, comment='同级排序(升序，建议留 10/20/30 间隔便于插入)')
    required = Column(Boolean, nullable=False, default=False, comment='是否必填（前端信息完整性提示用）')
    allow_custom = Column(Boolean, nullable=False, default=False, comment='是否允许在其下增补项目自定义子节点')
    config = Column(JSON, nullable=True, comment='节点配置: 下拉选项、单位、占位提示等')
    status = Column(String(16), nullable=False, default='active', comment='状态: active/disabled（停用保留历史与值，仅隐去）')
    created_by = Column(String(64), nullable=True, comment='创建人登录名（全局节点为管理员）')
    created_at = Column(String(30), nullable=False, comment='创建时间')
    updated_by = Column(String(64), nullable=True, comment='最近修改人登录名')
    updated_at = Column(String(30), nullable=False, comment='更新时间')

    __table_args__ = (
        # 树查询固定按「全局节点 ∪ 本项目节点」取行，再按 parent 排序组装
        Index('idx_pin_scope', 'project_id', 'status'),
        Index('idx_pin_parent_sort', 'project_id', 'parent_id', 'sort_order'),
        # 唯一性只在此处放一道「同项目内 node_key 不重复」的兜底；
        # 全局作用域（project_id IS NULL）的唯一性 MySQL 表达不了——NULL 不参与唯一比较，
        # UNIQUE(project_id, node_key) 那一半形同虚设。全局 key 唯一性由 Service 层校验
        # （info_node_service._assert_key_unique），见 SKILL 第 6 节。
        Index('uq_pin_project_key', 'project_id', 'node_key', unique=True),
    )

    def __repr__(self):
        scope = self.project_id or 'GLOBAL'
        return f"<ProjectInfoNode(id='{self.id}', scope={scope}, key='{self.node_key}')>"


class ProjectInfoValue(Base):
    """项目信息树上「某个项目的某个节点的当前值」。

    一个项目对一个节点只有一份当前值 → UNIQUE(project_id, node_id)。
    节点（字段定义）与值（项目数据）分开存，是这套结构的核心：
    全局节点能被所有项目共用，各项目的值互不可见。

    **不预创建空值**（见 SKILL 第 7.2 节）：200 个字段的项目如果没填，
    这里就该是 0 行而不是 200 行空记录；查询时 LEFT JOIN，无行即空值。

    value_json 存原生 JSON（裸字符串、数字、数组、对象都合法），
    具体形状由节点的 value_type 决定，与 config 里的选项定义配合。
    写值必须与写 project_info_value_history 在同一事务里完成。
    """
    __tablename__ = 'project_info_value'

    id = Column(String(64), primary_key=True, comment='记录UUID')
    project_id = Column(String(64), nullable=False, comment='值所属项目ID')
    node_id = Column(String(64), nullable=False, comment='对应的节点ID（全局节点或本项目增补节点）')
    value_json = Column(JSON, nullable=True, comment='节点值(原生JSON: 字符串/数字/数组/对象)')
    created_at = Column(String(30), nullable=False, comment='创建时间')
    updated_at = Column(String(30), nullable=False, comment='更新时间')
    updated_by = Column(String(64), nullable=True, comment='最近修改人登录名')

    __table_args__ = (
        Index('uq_piv_project_node', 'project_id', 'node_id', unique=True),
        Index('idx_piv_node', 'node_id'),
    )

    def __repr__(self):
        return f"<ProjectInfoValue(project='{self.project_id}', node='{self.node_id}')>"


class ProjectInfoValueHistory(Base):
    """项目信息值的变更历史（**唯一**历史表：值变动与结构变动都记在这里）。

    每一行是「谁在什么时候把哪个节点的值从什么改成了什么」。
    old_value / new_value 存原生 JSON，与 project_info_value.value_json 同尺度，
    便于做前后对比。

    必须同时记 project_id 与 node_id（见 SKILL 第 8 节）：同一个全局节点的值
    在项目 A / B / C 各有各的历史，只存 node_id 会把三个项目的历史混成一条线。

    node_key / node_name / node_type 是**写入时的快照**：节点被改名或停用后，
    历史列表仍能显示当时这个节点叫什么、是什么类型，不依赖节点行还活着。

    operation_type 覆盖两类操作：
      值变动  → create / update / delete
      结构变动 → node_create / node_move / node_rename
    parent_id 与 detail 供前端历史列表展示用：删除记录挂在被删节点的上级上，
    detail 是服务端拼好的人话描述。

    ⚠ 注意：old_value / new_value 目前**明文存储**节点值，节点值包含服务器与
    远端账号口令（SSH / ToDesk / 服务器登录密码）——这是产品经理明确拍板的决定。
    接入字段级加密时以本表为改造点（SKILL 第 14 节）。
    """
    __tablename__ = 'project_info_value_history'

    id = Column(String(64), primary_key=True, comment='记录UUID（时间有序，可当水位比较）')
    project_id = Column(String(64), nullable=False, comment='所属项目ID（历史按项目隔离，必填）')
    node_id = Column(String(64), nullable=True, comment='被操作的节点ID；整树级操作为 NULL')
    parent_id = Column(String(64), nullable=True, comment='上级节点ID（删除记录据此在父节点历史里展示）')
    node_key = Column(String(191), nullable=False, default='', comment='写入时的节点标识快照')
    node_name = Column(String(255), nullable=False, default='', comment='写入时的节点名称快照')
    node_type = Column(String(16), nullable=True, comment='写入时的节点类型快照')
    old_value = Column(JSON, nullable=True, comment='变更前的值（原生JSON）')
    new_value = Column(JSON, nullable=True, comment='变更后的值（原生JSON）')
    operation_type = Column(String(16), nullable=False, comment='操作类型: create/update/delete/node_create/node_move/node_rename')
    changed_by = Column(String(64), nullable=True, comment='操作人登录名（识别不到时为 NULL）')
    changed_by_name = Column(String(64), nullable=True, comment='操作人显示名')
    change_reason = Column(Text, nullable=True, comment='变更原因（预留，当前多数为空）')
    detail = Column(Text, nullable=True, comment='具体变动的人话描述（服务端拼好，前端直接展示）')
    changed_at = Column(String(30), nullable=False, comment='操作时间')

    __table_args__ = (
        Index('idx_pivh_project_time', 'project_id', 'changed_at'),
        Index('idx_pivh_project_node_time', 'project_id', 'node_id', 'changed_at'),
        Index('idx_pivh_parent', 'project_id', 'parent_id'),
    )

    def __repr__(self):
        return (f"<ProjectInfoValueHistory(id='{self.id}', project='{self.project_id}', "
                f"node='{self.node_id}', op='{self.operation_type}')>")


class ProjectInfoNodeMark(Base):
    """项目信息树节点「关注」标注（个人订阅：项目动态按人过滤）。

    用户在项目详情页「项目信息管理」展示卡上点子节点右侧的星标即关注该节点，
    被关注节点的最新一条变动展示在同页「项目动态」卡里。

    **每人一份关注列表**：主键 (node_id, operator)——同一节点可被多人各存一行，
    星标状态与项目动态都按当前登录人过滤（自己关注的自己才能看到）；
    按人隔离靠登录名（JWT sub），取不到用户身份的请求接口层拒绝（401）。

    注意 node_id 的语义：新结构下节点定义全局唯一，星标因此记的是**节点身份**，
    不随项目复制（旧结构里每个项目一份节点副本，星标也只对那一份有效）。
    关注仍按项目维度存储与查询：同一节点在项目 A 未关注、在项目 B 已关注是允许的。
    节点停用（status=disabled）或增补节点被删时，**所有人**的相关标注
    随节点一起清理，避免留下点不开的「孤儿关注」（见 info_node_mark_service）。
    """
    __tablename__ = 'project_info_node_mark'

    node_id = Column(String(64), primary_key=True, comment='被关注的节点ID')
    operator = Column(String(64), primary_key=True, comment='关注人登录名（关注列表按人隔离）')
    project_id = Column(String(64), nullable=False, comment='所属项目ID')
    operator_name = Column(String(64), nullable=True, comment='关注人显示名')
    created_at = Column(String(30), nullable=False, comment='关注时间')

    __table_args__ = (
        Index('idx_pnm_project_user', 'project_id', 'operator'),
    )

    def __repr__(self):
        return (f"<ProjectInfoNodeMark(node_id='{self.node_id}', "
                f"operator='{self.operator}', project_id='{self.project_id}')>")


class ProjectPin(Base):
    """项目「置顶」标注（个人置顶：项目进度管理页长按卡片 → 置顶）。

    项目进度管理页的项目列表按「置顶优先」排序，置顶的项目排在最前（同一人置顶多个时
    按置顶时间新的在前）。置顶入口与删除同在一个长按操作卡里。

    **每人一份置顶列表**：主键 (project_id, operator)——同一个项目可被多人各存一行，
    列表顺序按当前登录人过滤（自己置顶的只有自己看得见）。按人隔离靠登录名（JWT sub），
    取不到用户身份的请求接口层拒绝（401），与 project_info_node_mark（节点关注）同口径。

    项目被删除（软删）时本方标注一并清理，避免列表里出现点不开的「孤儿置顶」；
    清理只是保持数据整洁——软删的项目本来也不会进入任何项目列表。
    """
    __tablename__ = 'project_pin'

    project_id = Column(String(64), primary_key=True, comment='被置顶的项目ID')
    operator = Column(String(64), primary_key=True, comment='置顶人登录名（置顶列表按人隔离）')
    operator_name = Column(String(64), nullable=True, comment='置顶人显示名')
    created_at = Column(String(30), nullable=False, comment='置顶时间')

    __table_args__ = (
        Index('idx_project_pin_operator', 'operator', 'created_at'),
    )

    def __repr__(self):
        return (f"<ProjectPin(project_id='{self.project_id}', "
                f"operator='{self.operator}')>")


class ProjectBlockingConfig(Base):
    """项目「核心阻滞工单」AI 配置（项目详情页-项目工单卡）。

    管理员/超级管理员在卡片上点「配置阻滞权重」输入提示词后，后端把项目基础字段 +
    该项目工单基础数据 + 提示词交给大模型判定「当前项目最重要的阻滞工单」，
    判定结果（工单ID 列表 + 理由）存本表，卡片据此展示核心阻滞工单。

    每个项目一行（project_id 主键，重新配置即覆盖）；未配置的项目由服务层
    按默认规则（未完成工单按优先级/超期排序）挑候选，不落表。
    """
    __tablename__ = 'project_blocking_config'

    project_id = Column(String(64), primary_key=True, comment='项目ID')
    prompt = Column(Text, nullable=False, comment='管理员输入的阻滞判定提示词（权重说明）')
    ai_result = Column(Text, nullable=True, comment='AI 判定结果(JSON: ticket_ids/summary/reasons)')
    updated_by = Column(String(64), nullable=True, comment='最后配置人登录名')
    updated_by_name = Column(String(64), nullable=True, comment='最后配置人显示名')
    updated_at = Column(String(30), nullable=False, comment='配置时间')

    def __repr__(self):
        return f"<ProjectBlockingConfig(project_id='{self.project_id}')>"


