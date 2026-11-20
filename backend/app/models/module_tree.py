"""「产品→界面→功能」责任模块树 ORM 模型。

设计：一个产品一行（产品名为主键），树结构以 JSON 存储——
`tree_json` 存该产品的 interfaces 数组（界面→功能树），工程师通过 each 功能树的 engineers 字段关联。

对应 AI Assigner 的 module_tree[产品] = {"interfaces": [...]}。
DB 为主数据；保存后由后端服务导出覆盖 config.yaml 作为启动快照供 Assigner 读取。
"""
from sqlalchemy import Column, String, JSON, DateTime
from sqlalchemy.sql import func

from app.models.base import Base


class ModuleTree(Base):
    __tablename__ = "module_trees"

    # 产品名（唯一主键），如 调度USP / 摇人吧服务号 / 车端软件 / 车端硬件
    product = Column(String(64), primary_key=True, comment="产品名（唯一标识）")

    # 该产品的接口树：接口列表 array，每个接口含 key/name/description/functions[]
    # functions[] 每个功能含 key/name/keywords/anchor/engineers
    tree_json = Column(JSON, nullable=False, comment="产品→界面→功能 树（JSON）")

    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="最后更新时间")
