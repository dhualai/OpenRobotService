"""可达 USP 内网环境。

SSH 密码只给服务端拉日志使用，管理接口只返回是否已填写。
"""
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, JSON, String, Text

from app.models.base import Base


class UspEnv(Base):
    __tablename__ = "usp_env"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(128), nullable=False, comment="环境名称")
    code = Column(String(64), nullable=True, unique=True, comment="环境代号，空则不参与唯一")
    enabled = Column(Boolean, nullable=False, default=True, comment="是否启用")
    notes = Column(Text, nullable=True, comment="备注")
    project_id = Column(String(64), nullable=True, comment="预留关联项目")
    ssh_host = Column(String(255), nullable=False, comment="SSH 主机")
    ssh_port = Column(Integer, nullable=False, default=22, comment="SSH 端口")
    ssh_user = Column(String(128), nullable=False, comment="SSH 用户")
    ssh_auth_type = Column(String(16), nullable=False, default="password", comment="key 或 password")
    ssh_private_key_path = Column(String(512), nullable=True, comment="私钥路径")
    ssh_password = Column(Text, nullable=True, comment="SSH 密码密文，接口不回传")
    ssh_connect_timeout_s = Column(Float, nullable=False, default=8.0, comment="连接超时秒")
    export_script = Column(String(512), nullable=False, comment="export_logs 脚本路径")
    export_workdir = Column(String(512), nullable=False, comment="脚本可写目录")
    log_interval_min = Column(Integer, nullable=False, default=15, comment="拉日志间隔分钟")
    docker_container = Column(String(128), nullable=True, comment="Docker 容器名")
    docker_sudo = Column(Boolean, nullable=False, default=False, comment="docker 是否加 sudo")
    capabilities = Column(JSON, nullable=False, comment="能力列表")
    created_at = Column(DateTime, nullable=False, comment="创建时间 UTC")
    updated_at = Column(DateTime, nullable=False, comment="更新时间 UTC")
