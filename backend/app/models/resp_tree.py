"""责任双树 · 通用树节点 + 人挂载 ORM。

背景（`.local/责任双树/责任双树实现计划.md` §1.1）：这棵树此前只存在浏览器
localStorage 与 `.local/责任双树/*.json` 快照里，加载时把多份副本按「最富的当
base」互相合并 —— 2026-10-15、10-16 各丢过一次组织树。落库后 DB 是唯一真相源。

三张表：

- `resp_trees`      树实例（kind=org|skill 两棵），`revision` 作乐观锁
- `resp_nodes`      通用节点（不限层）。**同一公司可在多个域下各存一条** —— 现状即如此
                    （「浙江中力机械」在车体硬件与车端软件下各一条、部门集合不同），是保真要求
- `resp_node_links` 人挂载。`link_type`(solid/dotted) × `role`(leader/owner/specialist/member) 正交

软删口径：**节点软删**（`is_deleted` / `deleted_at`，运营误删可恢复，仓库先例见
`app/models/conversation.py`、`app/models/resource.py`）；**挂载硬删** —— 挂载是高频
增删的开关式数据，若软删则 `(user_id, node_id)` 唯一键会挡住「取消后再挂回同一个人」。
"""
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.models.base import Base

# node_kind 取值：树内的分组节点与内容节点
NODE_KINDS = ("umbrella", "module", "leaf", "org", "func")
# link_type × role 两个维度正交（2026-10-15 口径）
LINK_TYPES = ("solid", "dotted")
LINK_ROLES = ("leader", "owner", "specialist", "member")
LINK_SOURCES = ("manual", "synced_from_dept", "inferred", "migrated_from_engineers")


class RespTree(Base):
    """树实例：kind 只有 org / skill 两种。"""

    __tablename__ = "resp_trees"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    kind = Column(String(16), nullable=False, index=True, comment="树类型：org / skill")
    name = Column(String(128), nullable=False, comment="树名，如「公司组织」「技能·六域」")
    status = Column(String(16), nullable=False, server_default="draft", comment="draft / approved")
    # 乐观锁：该树任何节点/挂载变更都 +1；带 expected_revision 的写若不符则 409
    revision = Column(Integer, nullable=False, server_default="0", comment="乐观锁版本号")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="最后更新时间")


class RespNode(Base):
    """通用树节点，不限层。"""

    __tablename__ = "resp_nodes"
    __table_args__ = (
        Index("ix_resp_nodes_tree_parent", "tree_id", "parent_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    # 约束显式命名：create_all 自动生成的会叫 resp_nodes_ibfk_1，跟建表脚本里的名字对不上，
    # 结构校验（scripts/create_resp_trees.py）就会误报缺约束
    tree_id = Column(Integer, ForeignKey("resp_trees.id", ondelete="CASCADE",
                                         name="fk_resp_nodes_tree"),
                     nullable=False, comment="所属树")
    parent_id = Column(Integer, nullable=True, comment="父节点；null = 根")
    name = Column(String(255), nullable=False, comment="展示名")
    description = Column(Text, nullable=True, comment="业务描述（负责/不负责/典型现象）")
    keywords = Column(JSON, nullable=True, comment="关键词数组")
    anchor = Column(Text, nullable=True, comment="技能树兼容旧 anchor")
    owners = Column(JSON, nullable=True, comment="编制负责人 user_id 数组")
    # 同表跨树弱关联（组织组 ↔ 技能域），不加 FK，由应用层维护
    linked_node_id = Column(Integer, nullable=True, comment="跨树弱关联节点 id")
    node_kind = Column(String(16), nullable=False, server_default="leaf",
                       comment="umbrella / module / leaf / org / func")
    org_kind = Column(String(16), nullable=True, comment="org 节点细分：company / department")
    vehicle_models = Column(String(255), nullable=True, comment="负责车型")
    sort_order = Column(Integer, nullable=False, server_default="0", comment="同级排序")
    legacy_ref = Column(JSON, nullable=True, comment="迁移留痕，如 {old_id: 'org_xxx'}")
    is_deleted = Column(Boolean, nullable=False, server_default="0", comment="软删标记")
    deleted_at = Column(DateTime, nullable=True, comment="软删时间")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="最后更新时间")


class RespNodeLink(Base):
    """人挂在节点上：link_type × role 正交。"""

    __tablename__ = "resp_node_links"
    __table_args__ = (
        UniqueConstraint("user_id", "node_id", name="uq_resp_link_user_node"),
        Index("ix_resp_node_links_node_id", "node_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    user_id = Column(String(50), nullable=False, comment="工程师 users.id")
    node_id = Column(Integer, ForeignKey("resp_nodes.id", ondelete="CASCADE",
                                         name="fk_resp_links_node"),
                     nullable=False, comment="挂载节点 id")
    link_type = Column(String(16), nullable=False, server_default="solid",
                       comment="solid / dotted（虚线不进 Step1 hard 池）")
    role = Column(String(16), nullable=False, server_default="member",
                  comment="leader / owner / specialist / member")
    source = Column(String(32), nullable=False, server_default="manual",
                    comment="manual / synced_from_dept / inferred / migrated_from_engineers")
    confidence = Column(Float, nullable=True, comment="推断置信度（inferred 时）")
    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="最后更新时间")
