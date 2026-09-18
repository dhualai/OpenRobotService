"""责任模块树 · 功能级行模型 ORM。

对比旧表 `module_trees`（每产品一行、tree_json 存整树）：
- 本表**每功能一行**，用户改一个功能 = 只更新这一行，天然并发安全（多人改不同功能互不覆盖）。
- **不用 key**（旧 func_key/iface_key 由"中文名前两字拼音+hash"生成、改名会漂移），改用数据库自增主键 `id` 唯一定位；
- 界面以 `iface_name` + `iface_order` 冗余在功能行上，读取时按 product 分组、(iface_order, func_order) 排序聚合回"产品→界面→功能"树。

唯一约束：`(product, func_name)` 唯一（沿用"每功能名产品内唯一"语义）。
"""
from sqlalchemy import Column, String, Integer, JSON, Text, DateTime, UniqueConstraint
from sqlalchemy.sql import func

from app.models.base import Base


class ModuleTreeNode(Base):
    __tablename__ = "module_tree_nodes"
    __table_args__ = (
        UniqueConstraint("product", "func_name", name="uq_module_tree_nodes_product_funcname"),
    )

    # 主键：唯一定位点（前端编辑/审批都用它）
    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键")
    # 产品名（如 调度USP / 摇人吧服务号）
    product = Column(String(64), nullable=False, index=True, comment="产品名")
    # 界面名（作为分组/展示键）
    iface_name = Column(String(128), nullable=False, comment="界面名")
    # 界面排列序号（聚合时决定界面顺序）
    iface_order = Column(Integer, nullable=False, server_default="0", comment="界面排列序号")
    # 功能名
    func_name = Column(String(128), nullable=False, comment="功能名")
    # 界面内功能排列序号
    func_order = Column(Integer, nullable=False, server_default="0", comment="界面内功能排列序号")
    # 关键词数组
    keywords = Column(JSON, nullable=True, comment="关键词数组")
    # 功能描述/锚文本
    anchor = Column(Text, nullable=True, comment="功能描述/锚文本")
    # 负责工程师 id 数组
    engineers = Column(JSON, nullable=True, comment="负责工程师 id 数组")

    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="最后更新时间")
