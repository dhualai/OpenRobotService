from __future__ import annotations

from sqlalchemy.orm import Session

from app.infra.models import User
from app.services.names import DIRECTORY


def apply_directory(db: Session) -> None:
    """只回填显示名，**不创建任何账号**。

    账号与权限统一由摇人吧登录态提供（见 ``app/services/ors_auth.py``）：
    本服务不再自带内置账号、口令或授权，避免出现脱离摇人吧权限体系的第二入口。
    """
    for uname, name in DIRECTORY.items():
        row = db.get(User, uname)
        if row and not (row.display_name or "").strip():
            row.display_name = name
