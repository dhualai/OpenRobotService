"""「责任模块树」编辑审批单 ORM 模型。

背景：功能模块的负责人（engineers）决定了它的编辑权限——
   - 自己负责 / 待分配 / 管理员 / 有特殊权限 → 可直接修改
   - 已被他人负责 → 需原负责人同意（生成一条审批单）

一张审批单 = 对「某个产品 → 某界面 → 某功能」的一次修改请求（pending 待批准）。
审批通过后，由后端把那一段新值应用到 DB 并导出 config.yaml。
"""
from sqlalchemy import Column, String, Integer, JSON, DateTime, Text
from sqlalchemy.sql import func

from app.models.base import Base


class ModuleTreeEdit(Base):
    __tablename__ = "module_tree_edits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 功能定位：产品 + 界面的 key + 功能的 key（key 在保存时稳定生成）
    product = Column(String(64), nullable=False, comment="产品名")
    iface_key = Column(String(128), nullable=False, comment="界面 key")
    func_key = Column(String(128), nullable=False, comment="功能 key")

    # 修改内容：old / new（仅存本功能节点自身的 JSON：name/keywords/anchor/engineers）
    old_json = Column(JSON, nullable=True)
    new_json = Column(JSON, nullable=True)

    # 发起人
    requester_id = Column(String(64), nullable=False, comment="发起人用户 id")
    requester_name = Column(String(128), nullable=True, comment="发起人姓名")

    # 需要同意的人：即该功能原负责人 id 列表（JSON 数组），任一同意即可通过
    owner_ids = Column(JSON, nullable=True, comment="需同意的人（原负责人 id 列表）")

    # 状态：pending / approved / rejected / cancelled
    status = Column(String(16), nullable=False, default="pending", comment="审批状态")

    decider_id = Column(String(64), nullable=True, comment="审批人（通过的负责人）id")
    decision_note = Column(Text, nullable=True, comment="审批备注")

    created_at = Column(DateTime, server_default=func.now(), comment="创建时间")
    decided_at = Column(DateTime, nullable=True, comment="审批时间")
