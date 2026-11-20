"""企业微信表格数据源 + 记录镜像表。

背景：每接一张企微智能表格就要复制一份 adapter + route + DAG（见
`sources/wecom/adapter.py` 里的硬编码 URL / 30 个中文列 / 写死 project 表）。
本模块把「接一张表」拆成两块：

- `WecomSheetSource`：一张表格的连接与调度配置，页面 CRUD。
- `ExternalRecord`：所有表格共用的**记录镜像**，以 JSON 存原始 values，
  靠 `raw_hash` 判变。业务表（如 project）由落地策略从镜像加工，
  不再让每来一张新表就建一张物理表 + 一次迁移。

镜像键固定用企微原生的 `record_id`（最稳），不用业务列（列名会被人改）。
"""
from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text
from sqlalchemy import UniqueConstraint

from app.models.base import Base


class WecomSheetSource(Base):
    """一张企业微信智能表格 = 一行。"""

    __tablename__ = "wecom_sheet_source"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(64), nullable=False, unique=True, comment="数据源唯一标识，如 usp_projects")
    display_name = Column(String(128), nullable=False, comment="展示名")
    docid = Column(String(128), nullable=False, comment="企微智能表格文档 ID")
    sheet_id = Column(String(128), nullable=False, comment="子表 ID")
    enabled = Column(Boolean, nullable=False, default=True, comment="是否启用定时同步")
    # mirror：只落镜像表（M1）。handler：M2 起交给注册的落地策略加工进业务表。
    target_mode = Column(String(16), nullable=False, default="mirror", comment="mirror | handler")
    handler_key = Column(String(64), nullable=True, comment="target_mode=handler 时的策略标识")
    sync_interval_min = Column(Integer, nullable=False, default=30, comment="同步间隔分钟（供 DAG 参考）")
    notes = Column(Text, nullable=True, comment="备注")

    last_sync_at = Column(DateTime, nullable=True, comment="上次同步完成时间 UTC")
    last_stats = Column(JSON, nullable=True, comment="上次同步统计 {fetched,created,updated,unchanged}")
    last_error = Column(Text, nullable=True, comment="上次同步错误摘要")

    created_at = Column(DateTime, nullable=False, comment="创建时间 UTC")
    updated_at = Column(DateTime, nullable=False, comment="更新时间 UTC")


class ExternalRecord(Base):
    """任意外部表格的一行镜像。

    所有 source 共用一张表，靠 source_key 隔离；values 保留拍扁后的原始结构。
    """

    __tablename__ = "external_record"
    __table_args__ = (
        UniqueConstraint("source_key", "record_id", name="uq_external_record_src_record"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_key = Column(String(64), nullable=False, index=True, comment="wecom_sheet_source.key")
    record_id = Column(String(128), nullable=False, comment="企微记录 ID（镜像主键）")
    values = Column(JSON, nullable=False, comment="拍扁后的字段值 {列名: 值}")
    raw_hash = Column(String(64), nullable=False, comment="values 规范化哈希，用于判变")
    pulled_at = Column(DateTime, nullable=False, comment="本次拉到该行的时间 UTC")
    changed_at = Column(DateTime, nullable=True, comment="内容最后一次变化的时间 UTC")
    created_at = Column(DateTime, nullable=False, comment="首次入库时间 UTC")
