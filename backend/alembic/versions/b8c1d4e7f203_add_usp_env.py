"""add usp_env

可达 USP 内网环境从仓库外的 json 文件改为数据库表。
启动时 create_all 可能已经建过表，这里先查再建，避免重复执行报错。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


revision: str = "b8c1d4e7f203"
down_revision: Union[str, None] = "a7d3e5b91c24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "usp_env"


def _table_exists() -> bool:
    bind = op.get_bind()
    result = bind.execute(text(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = :t"
    ), {"t": TABLE}).scalar()
    return bool(result)


def upgrade() -> None:
    if _table_exists():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False, comment="环境名称"),
        sa.Column("code", sa.String(length=64), nullable=True, comment="环境代号，空则不参与唯一"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true(), comment="是否启用"),
        sa.Column("notes", sa.Text(), nullable=True, comment="备注"),
        sa.Column("project_id", sa.String(length=64), nullable=True, comment="预留关联项目"),
        sa.Column("ssh_host", sa.String(length=255), nullable=False, comment="SSH 主机"),
        sa.Column("ssh_port", sa.Integer(), nullable=False, server_default="22", comment="SSH 端口"),
        sa.Column("ssh_user", sa.String(length=128), nullable=False, comment="SSH 用户"),
        sa.Column("ssh_auth_type", sa.String(length=16), nullable=False, server_default="password", comment="key 或 password"),
        sa.Column("ssh_private_key_path", sa.String(length=512), nullable=True, comment="私钥路径"),
        sa.Column("ssh_password", sa.Text(), nullable=True, comment="SSH 密码密文，接口不回传"),
        sa.Column("ssh_connect_timeout_s", sa.Float(), nullable=False, server_default="8", comment="连接超时秒"),
        sa.Column("export_script", sa.String(length=512), nullable=False, comment="export_logs 脚本路径"),
        sa.Column("export_workdir", sa.String(length=512), nullable=False, comment="脚本可写目录"),
        sa.Column("log_interval_min", sa.Integer(), nullable=False, server_default="15", comment="拉日志间隔分钟"),
        sa.Column("docker_container", sa.String(length=128), nullable=True, comment="Docker 容器名"),
        sa.Column("docker_sudo", sa.Boolean(), nullable=False, server_default=sa.false(), comment="docker 是否加 sudo"),
        sa.Column("capabilities", sa.JSON(), nullable=False, comment="能力列表"),
        sa.Column("created_at", sa.DateTime(), nullable=False, comment="创建时间 UTC"),
        sa.Column("updated_at", sa.DateTime(), nullable=False, comment="更新时间 UTC"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_usp_env_code"),
    )


def downgrade() -> None:
    if not _table_exists():
        return
    op.drop_table(TABLE)
