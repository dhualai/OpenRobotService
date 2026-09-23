"""界面图鉴：标准截图 + 难懂区域人工标注（供 VLM 解读挂载）。

regions JSON 元素约定：
  {id, x, y, w, h, question, answer, status}
  坐标为相对图宽高的 0～1；status: pending|answered|skipped
"""
from sqlalchemy import Column, Integer, String, Text, JSON, DateTime, text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.sql import func

from app.models.base import Base


class UiAtlasCard(Base):
    __tablename__ = "ui_atlas_cards"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product = Column(String(64), nullable=False, index=True, comment="产品名，如 调度USP")
    iface_name = Column(String(128), nullable=False, index=True, comment="界面名，如 监控")
    # data URI 可能很大；MySQL 用 MEDIUMTEXT，其它方言退回 Text
    image_url = Column(
        Text().with_variant(MEDIUMTEXT(), "mysql"),
        nullable=False,
        comment="标准图 URL 或 data URI",
    )
    page_caption = Column(Text, nullable=True, comment="整页一句话说明（可选）")
    source = Column(
        String(32), nullable=False, default="upload",
        comment="来源：upload|kb（预留知识库增量）",
    )
    kb_path = Column(String(512), nullable=True, comment="知识库路径预留")
    status = Column(
        String(32), nullable=False, default="draft", index=True,
        comment="draft|pending_answers|published",
    )
    regions = Column(JSON, nullable=False, default=list, comment="难懂区域列表")
    # 用 CURRENT_TIMESTAMP 而非 func.now()：SQLAlchemy 在 MySQL >= 8.0.13 上会把
    # func.now() 渲染成表达式默认值 DEFAULT (now())，而 8.0.13 对含该默认值的表
    # 执行 ALTER（CREATE INDEX 等）会报 1067 Invalid default value。
    created_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, server_default=text("CURRENT_TIMESTAMP"), onupdate=func.now())
