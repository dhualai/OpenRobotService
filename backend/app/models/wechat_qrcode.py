"""微信公众号带参数二维码实体表。

与 `create_qrcode_ticket` (WechatService) 配合：
- 后端 CRUD 管理二维码生命周期
- 批量创建时循环调微信接口，结果逐行落库
- 状态机 4 态：init → entering → published → deprecated
  （录入信息行保存后直接 published）

永久码微信侧最多 10 万个，`is_permanent` + `batch_id` 便于管控配额。
"""

from sqlalchemy import Column, Integer, String, DateTime, Text, Index
from sqlalchemy.sql import func

from app.models.base import Base


class QrcodeStatus:
    """二维码状态常量（纯字符串，避免 MySQL ENUM 加值需 DDL）。"""

    INIT = "init"                # 初始化：场景值已定义，尚未调微信接口
    ENTERING = "entering"        # 录入中：已调微信创建 ticket，待人工核对
    PUBLISHED = "published"      # 已发布：对外使用中
    DEPRECATED = "deprecated"    # 已弃用：停止使用，保留历史


class QrcodeType:
    """二维码类型（临时/永久）。"""

    TEMPORARY = "temporary"
    PERMANENT = "permanent"


class WechatQrcode(Base):
    """微信公众号带参数二维码实体。"""

    __tablename__ = "wechat_qrcodes"

    id = Column(Integer, primary_key=True, autoincrement=True, comment="主键；同时作为微信 scene_str（str(id)）")

    # ── 业务标识 ──
    name = Column(String(128), nullable=False, default="", comment="二维码名称/用途（如「智能体入口-客服A」）")
    description = Column(Text, nullable=True, comment="用途说明")

    # ── 微信返回 ──
    ticket = Column(String(256), nullable=True, comment="微信 ticket，凭此换二维码图片")
    url = Column(String(512), nullable=True, comment="微信短链（url 字段，非扫码跳转 URL）")
    qrcode_image_url = Column(String(512), nullable=True, comment="换图后的可访问地址（若上传到 CDN/OSS）")
    type = Column(String(16), nullable=False, default=QrcodeType.PERMANENT, comment="temporary/permanent")
    expire_seconds = Column(Integer, nullable=True, comment="临时码有效期（秒），永久码为 NULL")

    # ── 状态机 ──
    status = Column(String(20), nullable=False, default=QrcodeStatus.INIT, index=True, comment="状态")

    # ── 批量管理 ──
    batch_id = Column(String(64), nullable=True, index=True, comment="批次 ID，批量创建时同一批次共享")

    # ── 录入信息（其他项目登记，2026-09-29 用户口径） ──
    # 「新建项目 → 录入信息」一条录入 = 本表一行：项目编号/项目名/项目地点/客户名/车型
    # 五个字段和行 id 同行存（见 admin/api/qrcode.py 的 project-info 两个接口）。
    # 项目id 就是行 id（str(id)，2026-09-30 口径：scene 改 str(id) 自动生成，不再单独占列，
    # 也不再有「扫码关联 USP 项目」的 project_id 列）；project_code 非空 = 这是一条
    # 录入信息行，项目编号的「唯一」由接口层只在录入信息行范围内查重（不加物理唯一索引）。
    project_code = Column(String(64), nullable=True, index=True, comment="项目编号（录入信息行；唯一由接口层查重）")
    project_name = Column(String(128), nullable=True, comment="项目名（录入信息行自带）")
    project_location = Column(String(128), nullable=True, comment="项目地点（录入信息行）")
    customer_name = Column(String(128), nullable=True, comment="客户名（录入信息行）")
    vehicle_model = Column(String(128), nullable=True, comment="车型（录入信息行）")

    # ── 扫码跳转配置 ──
    redirect_url = Column(String(512), nullable=True, comment="扫码后跳转 URL（覆盖默认 /app/call）")

    # ── 操作人 ──
    created_by = Column(String(64), nullable=True, comment="创建人 username")
    published_by = Column(String(64), nullable=True, comment="发布人 username")
    deprecated_by = Column(String(64), nullable=True, comment="弃用人 username")

    # ── 时间戳 ──
    ticket_created_at = Column(DateTime, nullable=True, comment="ticket 创建时间（调微信接口返回时间）")
    created_at = Column(DateTime, server_default=func.now(), comment="记录创建时间")
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment="记录更新时间")

    __table_args__ = (
        Index("ix_wechat_qrcodes_status_type", "status", "type"),
    )

    @property
    def scene_str(self) -> str:
        """微信带参数二维码的场景值，始终等于 str(id)。"""
        return str(self.id)
