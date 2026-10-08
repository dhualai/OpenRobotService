"""AI 模块独立数据库连接（不依赖 backend）

读取 DATABASE_URL 的方式：
  1. 环境变量 DATABASE_URL
  2. 从 backend/app/core/.env 读取
  3. 默认值
"""
import os
from pathlib import Path
from sqlalchemy import Boolean, create_engine, Column, String, Integer, BigInteger, Text, DateTime, JSON, Index, UniqueConstraint, Enum as SQLEnum
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import func


# ai/core/database.py → parent=core → parent=ai → parent=项目根
_project_root = Path(__file__).resolve().parent.parent.parent


def _get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if url:
        return url
    # 尝试从 backend/.env 读取
    backend_env = _project_root / "backend" / ".env"
    if backend_env.exists():
        for line in backend_env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL") and "=" in line:
                url = line.split("=", 1)[1].strip().strip('"').strip("'")
                if url:
                    return url
    return "mysql+pymysql://root:123456@127.0.0.1:3306/helpdesk"


DATABASE_URL = _get_database_url()
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class User(Base):
    """用户表（仅查询，字段对齐 backend/app/models/identity.py 的 UserDB）"""
    __tablename__ = "users"

    id = Column(String(64), primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    name = Column(String(128), nullable=True)
    status = Column(String(32), default="inactive", nullable=False)


class Task(Base):
    """工单表（仅查询，字段对齐 backend/app/models/task.py；tickets 表已废弃，tasks 表即工单表）"""
    __tablename__ = "tasks"

    id = Column(BigInteger, primary_key=True, index=True, comment="任务ID")
    title = Column(String(255), nullable=False, index=True, comment="任务标题")
    description = Column(Text, nullable=False, comment="任务描述")
    task_type = Column(String(30), nullable=False, default="problem", index=True, comment="任务类型: problem/bug/feature/support/other")
    status = Column(String(30), nullable=False, default="new", index=True, comment="任务状态: new/in_progress/pending/resolved/canceled/closed")
    priority = Column(String(30), nullable=False, default="medium", index=True, comment="任务优先级: low/medium/high/urgent")
    created_by = Column(String(50), nullable=False, index=True, comment="创建者ID")
    assigned_to = Column(String(50), nullable=True, index=True, comment="处理者ID")
    customer = Column(String(100), nullable=True, comment="客户信息")
    team = Column(String(100), nullable=True, comment="所属团队")
    project_name = Column(String(255), nullable=True, index=True, comment="项目名称")
    project_id = Column(String(255), nullable=True, index=True, comment="项目ID")
    related_resource_id = Column(BigInteger, nullable=True, index=True, comment="关联资源ID")
    created_at = Column(DateTime, server_default=func.now(), nullable=False, comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), nullable=False, comment="更新时间")
    resolved_at = Column(DateTime, nullable=True, comment="解决时间")
    canceled_at = Column(DateTime, nullable=True, comment="取消时间")
    closed_at = Column(DateTime, nullable=True, comment="关闭时间")
    deadline_at = Column(DateTime, nullable=True, comment="截止时间")
    tags = Column(JSON, nullable=True, comment="标签列表")
    metadata_info = Column(JSON, nullable=True, comment="扩展元数据")
    attachments = Column(JSON, nullable=True, comment="附件列表")
    reply_count = Column(Integer, nullable=False, default=0, comment="回复数量")
    view_count = Column(Integer, nullable=False, default=0, comment="查看数量")
    source = Column(String(32), nullable=False, default="manual", index=True, comment="任务来源: manual/zentao/...")
    external_id = Column(String(64), nullable=True, index=True, comment="外部系统任务ID")
    external_url = Column(String(512), nullable=True, comment="外部系统跳转链接")
    # 当前步骤（关联 task_steps 模板；冗余存名称/结束时间便于直接展示，与 backend/app/models/task.py Task 对齐）
    curr_step_id = Column(BigInteger, nullable=True, index=True, comment="当前步骤ID")
    curr_step_name = Column(String(128), nullable=True, comment="当前步骤名称")
    curr_step_endtime = Column(DateTime, nullable=True, comment="当前步骤结束时间")
    step_last_updated_by = Column(String(100), nullable=True)
    step_last_updated_at = Column(DateTime, nullable=True)
    step_negotiation_round = Column(Integer, nullable=False, server_default="0")

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_task_source_external"),
    )


class ProjectDelivery(Base):
    """交付项目表（仅查询，字段对齐 backend/app/models/delivery.py Project）"""
    __tablename__ = "project"

    id = Column(String(64), primary_key=True, comment="项目ID/代码，与code一致")
    code = Column(String(64), unique=True, nullable=False, comment="项目代码")
    name = Column(String(128), nullable=False, comment="项目名称")

    system_id = Column(String(50), nullable=True, comment="系统ID")
    description = Column(Text, nullable=True, comment="项目描述")
    contact_person = Column(String(50), nullable=True, comment="对接人")
    contact_person_id = Column(String(64), nullable=True, comment="对接人ID（与 users.id 同长度）")
    project_contact = Column(String(50), nullable=True, comment="对接人")
    status = Column(String(20), nullable=False, default="active", comment="状态")
    expected_trend = Column(String(20), nullable=True, comment="预计走向")
    issues = Column(Integer, nullable=False, default=0, comment="问题数")
    risks = Column(Integer, nullable=False, default=0, comment="风险数")
    personnel_plan = Column(String(50), nullable=True, comment="人员计划")
    risk_list = Column(Text, nullable=True, comment="风险清单")
    deployment_date = Column(String(20), nullable=True, comment="部署时间")
    deployment_version = Column(String(50), nullable=True, comment="部署版本")
    recent_delivery_date = Column(String(20), nullable=True, comment="近期交付时间")
    recent_delivery_content = Column(Text, nullable=True, comment="近期交付内容")
    final_delivery_date = Column(String(20), nullable=True, comment="最终交付时间")
    project_summary = Column(Text, nullable=True, comment="项目总结")
    task_execution_status = Column(String(50), nullable=True, comment="任务执行情况")
    field_links = Column(Text, nullable=True, comment="字段链接(JSON格式)")
    category_basis = Column(String(20), nullable=False, default="重要紧急", comment="分类依据")
    # 项目扩展信息（递归嵌套 JSON），结构由服务层约定
    ext_info = Column(JSON, nullable=True, comment="项目扩展信息(递归嵌套 JSON)")
    # 乐观锁版本号（backend update_project 维护，AI 侧仅读取）
    version = Column(Integer, nullable=False, default=1, comment="乐观锁版本号")

    project_type = Column(String(20), nullable=True, comment="项目类型（企业微信项目类型字段原值）")
    stage_notes = Column(Text, nullable=True, comment="生命周期各阶段补充说明(JSON格式，键为阶段名)")
    risk_carrying_type = Column(String(20), nullable=True, comment="风险承接类型")
    special_attention = Column(Text, nullable=True, comment="特别关注说明")
    risk_task_description = Column(Text, nullable=True, comment="风险和任务描述")
    management_strategy = Column(Text, nullable=True, comment="项目管理策略")
    project_documents = Column(Text, nullable=True, comment="项目文档(JSON格式，[{name,resource_id,url}])")
    sales = Column(String(50), nullable=True, comment="销售")
    pre_sales = Column(String(50), nullable=True, comment="售前")
    project_manager = Column(String(50), nullable=True, comment="项目经理")
    project_manager_id = Column(String(64), nullable=True, comment="项目经理ID（与 users.id 同长度，用于关联角色）")
    field_engineer = Column(String(50), nullable=True, comment="实施工程师")

    internal_code = Column(String(50), nullable=True, comment="内部编号")
    project_region = Column(String(30), nullable=True, comment="项目区域/地点")
    total_vehicle_count = Column(Integer, nullable=True, comment="总车数")
    controller_vendor = Column(String(30), nullable=True, comment="控制器选择")
    system_integration = Column(Text, nullable=True, comment="系统/外设对接(JSON数组)")
    server_deployment_status = Column(String(30), nullable=True, comment="服务器部署")
    settlement_period = Column(String(20), nullable=True, comment="业绩核算期（手工填写，常见YYYYMM如202608，兼容YYYY-MM）")
    undertake_status = Column(String(10), nullable=False, default="是", comment="是否承接（是/待定；「否」不入库）")

    __table_args__ = (
        Index("idx_project_code", "code", unique=True),
        Index("idx_project_status", "status"),
        Index("idx_project_settlement_period", "settlement_period"),
        Index("idx_project_undertake_status", "undertake_status"),
    )


class ProjectInfoNode(Base):
    """项目信息树节点定义（仅查询，字段对齐 backend/app/models/delivery.py ProjectInfoNode）。

    双用途：project_id IS NULL = 全局模板节点（所有项目共享同一份字段定义）；
    project_id = A = 项目 A 的增补自定义节点（仅 A 可见）。
    节点只描述结构（叫什么、什么类型），不存值——项目实际数据在
    project_info_value，靠 (project_id, node_id) 关联。
    """
    __tablename__ = "project_info_node"

    id = Column(String(64), primary_key=True, comment="节点永久身份(UUID)，改名/挪位不换")
    project_id = Column(String(64), nullable=True, comment="NULL=全局模板节点；非 NULL=该项目专属的增补节点")
    parent_id = Column(String(64), nullable=True, comment="父节点ID, NULL=根节点")
    node_key = Column(String(191), nullable=False, comment="程序用稳定标识(如 base.customer_info)，建立后不可改")
    node_name = Column(String(255), nullable=False, comment="节点显示名")
    node_type = Column(String(16), nullable=False, default="field", comment="节点类型: root/group/field")
    value_type = Column(String(32), nullable=False, default="text", comment="值类型: text/number/boolean/date/select/multi_select/person/attachment/json")
    sort_order = Column(Integer, nullable=False, default=0, comment="同级排序(升序)")
    required = Column(Boolean, nullable=False, default=False, comment="是否必填")
    allow_custom = Column(Boolean, nullable=False, default=False, comment="是否允许在其下增补项目自定义子节点")
    config = Column(JSON, nullable=True, comment="节点配置: 下拉选项、单位、占位提示等")
    status = Column(String(16), nullable=False, default="active", comment="状态: active/disabled（停用保留历史与值，仅隐去）")
    created_by = Column(String(64), nullable=True, comment="创建人登录名（全局节点为管理员）")
    created_at = Column(String(30), nullable=False, comment="创建时间")
    updated_by = Column(String(64), nullable=True, comment="最近修改人登录名")
    updated_at = Column(String(30), nullable=False, comment="更新时间")


class ProjectInfoValue(Base):
    """项目信息值表（仅查询，字段对齐 backend/app/models/delivery.py ProjectInfoValue）。

    某个项目的某个节点的当前值：UNIQUE(project_id, node_id)，一个项目对一个节点
    只有一份当前值。不预创建空值——没填过的节点这里就没有行，查询时 LEFT JOIN。
    value_json 存原生 JSON，具体形状由节点的 value_type 决定。
    """
    __tablename__ = "project_info_value"

    id = Column(String(64), primary_key=True, comment="记录UUID")
    project_id = Column(String(64), nullable=False, comment="值所属项目ID")
    node_id = Column(String(64), nullable=False, comment="对应的节点ID（全局节点或本项目增补节点）")
    value_json = Column(JSON, nullable=True, comment="节点值(原生JSON: 字符串/数字/数组/对象)")
    created_at = Column(String(30), nullable=False, comment="创建时间")
    updated_at = Column(String(30), nullable=False, comment="更新时间")
    updated_by = Column(String(64), nullable=True, comment="最近修改人登录名")


class ProjectInfoValueHistory(Base):
    """项目信息值变更历史（仅查询，字段对齐 backend/app/models/delivery.py ProjectInfoValueHistory）。

    每一行是「谁在什么时候把哪个节点的值从什么改成了什么」。
    old_value / new_value 与 value_json 同尺度；node_key/node_name/node_type
    是写入时的快照，节点改名/停用后历史仍可追溯。
    operation_type 覆盖值变动 create/update/delete 与结构变动
    node_create/node_move/node_rename 两类操作。
    """
    __tablename__ = "project_info_value_history"

    id = Column(String(64), primary_key=True, comment="记录UUID（时间有序，可当水位比较）")
    project_id = Column(String(64), nullable=False, comment="所属项目ID（历史按项目隔离，必填）")
    node_id = Column(String(64), nullable=True, comment="被操作的节点ID；整树级操作为 NULL")
    parent_id = Column(String(64), nullable=True, comment="上级节点ID")
    node_key = Column(String(191), nullable=False, default="", comment="写入时的节点标识快照")
    node_name = Column(String(255), nullable=False, default="", comment="写入时的节点名称快照")
    node_type = Column(String(16), nullable=True, comment="写入时的节点类型快照")
    old_value = Column(JSON, nullable=True, comment="变更前的值（原生JSON）")
    new_value = Column(JSON, nullable=True, comment="变更后的值（原生JSON）")
    operation_type = Column(String(16), nullable=False, comment="操作类型: create/update/delete/node_create/node_move/node_rename")
    changed_by = Column(String(64), nullable=True, comment="操作人登录名")
    changed_by_name = Column(String(64), nullable=True, comment="操作人显示名")
    change_reason = Column(Text, nullable=True, comment="变更原因（预留）")
    detail = Column(Text, nullable=True, comment="具体变动的人话描述")
    changed_at = Column(String(30), nullable=False, comment="操作时间")


class ProjectInfoNodeMark(Base):
    """项目信息树节点「关注」标注（仅查询，字段对齐 backend/app/models/delivery.py ProjectInfoNodeMark）。

    每人一份关注列表：主键 (node_id, operator)，同一节点可被多人各存一行。
    project_id 注明关注发生在哪个项目维度。
    """
    __tablename__ = "project_info_node_mark"

    node_id = Column(String(64), primary_key=True, comment="被关注的节点ID")
    operator = Column(String(64), primary_key=True, comment="关注人登录名（关注列表按人隔离）")
    project_id = Column(String(64), nullable=False, comment="所属项目ID")
    operator_name = Column(String(64), nullable=True, comment="关注人显示名")
    created_at = Column(String(30), nullable=False, comment="关注时间")


class Risk(Base):
    """风险表（仅查询，字段对齐 backend/app/models/delivery.py Risk）"""
    __tablename__ = "risk"

    id = Column(Integer, primary_key=True, autoincrement=True)
    risk_code = Column(String(50), nullable=False, unique=True, comment="风险代码")
    project_code = Column(String(50), nullable=False, comment="项目代码")
    project_name = Column(String(100), nullable=False, comment="项目名称")
    risk_category = Column(String(50), nullable=False, comment="风险分类")
    custom_category = Column(String(50), nullable=True, comment="自定义分类")
    description = Column(String(1000), nullable=False, comment="风险描述")
    risk_level = Column(String(20), nullable=False, comment="风险等级")
    response_measure = Column(String(1000), nullable=True, comment="应对措施")
    progress = Column(String(100), nullable=True, comment="进度")
    responsible_person = Column(String(50), nullable=False, comment="负责人")
    responsible_person_id = Column(String(20), nullable=False, comment="负责人ID")
    status = Column(String(20), nullable=False, comment="状态")
    discovery_time = Column(String(20), nullable=False, comment="发现时间")
    close_time = Column(String(30), nullable=True, comment="关闭时间")
    created_at = Column(String(30), nullable=False, comment="创建时间")
    updated_at = Column(String(30), nullable=False, comment="更新时间")

    __table_args__ = (
        Index("idx_risk_project", "project_code", "project_name"),
        Index("idx_risk_status", "status"),
        Index("idx_risk_discovery_time", "discovery_time"),
    )


class CollectionData(Base):
    """采集数据表（仅查询，字段对齐 backend/app/models/delivery.py CollectionData）。

    存储各项目指标采集数据：project 为项目ID，indicator 为指标标签
    （如 GroupEfficiency 搬运效率），start_time_int / end_time_int 为采集
    窗口的秒级时间戳（与 backend iso_to_timestamp_ms 的落库口径一致），
    data 为各指标 JSON 数据。
    """
    __tablename__ = "collection_data"

    id = Column(Integer, primary_key=True)
    project = Column(String(50), nullable=False)
    indicator = Column(String(100), nullable=False)
    start_time_int = Column(BigInteger, nullable=False, comment="数据采集开始时间戳用于查询")
    end_time_int = Column(BigInteger, nullable=False, comment="数据采集结束时间戳用于查询")
    data = Column(Text, nullable=False)
    collection_time = Column(String(50), nullable=False)
    record_time = Column(String(50), nullable=False)
    time_str = Column(String(100), nullable=False)

    __table_args__ = (
        Index("idx_coll_unique_key", "project", "indicator", "start_time_int", "end_time_int"),
        Index("idx_coll_time", "start_time_int"),
    )


class UserProjectRole(Base):
    """用户-项目-角色关联表（仅查询，字段对齐 backend/app/models/identity.py）"""
    __tablename__ = "user_project_roles"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    project_id = Column(String(64), nullable=True, index=True)
    role_id = Column(String(64), nullable=True)
    report_to_id = Column(String(64), nullable=True)


class Conversation(Base):
    """会话表（对齐 backend/app/models/conversation.py）"""
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False, default="新会话", comment="会话标题")
    user_id = Column(String(255), nullable=False, default="", comment="用户ID")
    scene_type = Column(String(255), nullable=False, default="chat", comment="场景类型: chat/faq/support/consultation/other/dataqa")
    service_ticket_id = Column(String(255), nullable=False, default="", comment="关联工单ID")
    metadata_ = Column(Text, nullable=True, comment="元数据")
    is_deleted = Column(Boolean, nullable=False, default=False, server_default="0", comment="逻辑删除：1=用户已删除（列表隐藏，数据保留供统计）")
    deleted_at = Column(DateTime, nullable=True, comment="逻辑删除时间（UTC）")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")


class Message(Base):
    """消息表（对齐 backend/app/models/conversation.py Message）"""
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, nullable=False, comment="会话ID")
    role = Column(String(20), nullable=False, comment="角色: user/assistant/system")
    content = Column(Text, nullable=False, comment="消息内容")
    message_type = Column(String(20), nullable=False, default="text", comment="消息类型: text/image/file/audio/multimodal")
    file_urls = Column(Text, nullable=True, comment="文件URL列表")
    parent_message_id = Column(Integer, nullable=True, comment="父消息ID")
    sequence = Column(Integer, nullable=False, default=0, comment="消息序号")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    metadata_ = Column(Text, nullable=True, comment="元数据")


class TaskParticipant(Base):
    """任务参与人表（只读，字段对齐 backend/app/models/task.py TaskParticipant）。

    历史工单列表卡片「评论区参与人头像堆叠」的数据源；评论成功后由后端同事务幂等
    upsert 写入（见 backend 评论端点）。AI 侧仅查询，不写入。
    """
    __tablename__ = "task_participants"

    id = Column(BigInteger, primary_key=True, index=True, comment="参与记录ID")
    task_id = Column(BigInteger, nullable=False, index=True, comment="任务ID")
    username = Column(String(50), nullable=False, index=True, comment="参与人username")
    created_at = Column(DateTime, comment="首次参与时间")
    last_active_at = Column(DateTime, comment="最近一次参与时间")


class TaskCommentRead(Base):
    """评论已读游标表（只读，字段对齐 backend/app/models/task.py TaskCommentRead）。

    红点判定口径：存在「作者不是我、且 comment_id > 我的游标」的评论 ⇒ 该作者头像亮红点。
    """
    __tablename__ = "task_comment_read"

    id = Column(BigInteger, primary_key=True, index=True)
    task_id = Column(BigInteger, nullable=False, index=True, comment="任务ID")
    username = Column(String(50), nullable=False, index=True, comment="用户username")
    last_read_comment_id = Column(BigInteger, nullable=True, comment="已读到的最后一条评论ID")


class Vehicle(Base):
    """车辆档案表（AI 侧自有新表，扫码定制模式用）。

    车体二维码（服务链接+唯一车号）→ 用户扫码进入时，前端模式确认接口拿
    上游参数（车型/项目/客户）来这里校验：有档案 → 该会话注册为车型定制
    模式；无档案 → 报错拦住（实验阶段不降级常规模式）。
    AI 侧自有表（非 backend 映射），由 ai/api/vehicle_mode.py 首次使用时
    幂等建表；初版建档走手工 INSERT / 管理 SQL。
    """
    __tablename__ = "vehicles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    vehicle_code = Column(String(64), unique=True, nullable=False, comment="唯一车号（二维码绑定，如 XQE-122）")
    model = Column(String(64), nullable=False, index=True, comment="车型（如 XQE）")
    project_name = Column(String(128), nullable=True, index=True, comment="项目名")
    customer_name = Column(String(128), nullable=True, comment="客户名")
    location = Column(String(128), nullable=True, comment="地点")
    status = Column(String(16), nullable=False, default="active", comment="active/disabled")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="更新时间")

    __table_args__ = (
        Index("idx_vehicle_model_project", "model", "project_name"),
    )
