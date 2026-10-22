"""种子脚本：为工单门禁准备项目、机器人账号与权限。

工单门禁把 AI 生成的功能用例跑在真实后端上。真实后端必须有一个已知的项目和
一个已知的账号，否则生成用例里的「登录 → 调接口」必然失败（CI 里是一个全新的
空库，连默认管理员都不存在）。

本脚本幂等：已存在的项目/账号不会被重复创建。

用法：
    python backend/scripts/seed_automation_env.py

环境变量：
    SEED_PROJECT_ID     工单扫描使用的项目 id（默认 Leo_test）
    SEED_PROJECT_NAME   项目显示名（默认 摇人吧服务号-测试）
    SEED_BOT_USERNAME   生成用例登录用的账号（默认 automation_bot）
    SEED_BOT_PASSWORD   该账号的密码（默认 Automation@2026，仅用于一次性 CI 库）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.database import db_manager  # noqa: E402
from app.core.security import get_password_hash  # noqa: E402

DEFAULT_PROJECT_ID = "Leo_test"
DEFAULT_PROJECT_NAME = "摇人吧服务号-测试"
DEFAULT_BOT_USERNAME = "automation_bot"
DEFAULT_BOT_PASSWORD = "Automation@2026"


def seed_project(project_id: str, project_name: str) -> dict:
    """Create the ticket project when it is missing."""
    existing = db_manager.get_project(project_id)
    if existing:
        return {
            "project_id": project_id,
            "status": "exists",
            "name": existing.get("name"),
        }
    created = db_manager.add_project(project_id, project_id, project_name)
    return {
        "project_id": project_id,
        "status": "created" if created else "failed",
        "name": project_name,
    }


def seed_user(user_id: str, username: str, password: str, name: str) -> dict:
    """Create the automation account with full permissions when it is missing."""
    existing = db_manager.get_user(username)
    if existing:
        return {"username": username, "status": "exists"}
    created = db_manager.add_user(
        user_id=user_id,
        username=username,
        hashed_password=get_password_hash(password),
        permissions=["admin"],
        name=name,
        status="active",
    )
    return {"username": username, "status": "created" if created else "failed"}


def main() -> int:
    project_id = os.getenv("SEED_PROJECT_ID", DEFAULT_PROJECT_ID)
    project_name = os.getenv("SEED_PROJECT_NAME", DEFAULT_PROJECT_NAME)
    username = os.getenv("SEED_BOT_USERNAME", DEFAULT_BOT_USERNAME)
    password = os.getenv("SEED_BOT_PASSWORD", DEFAULT_BOT_PASSWORD)

    project = seed_project(project_id, project_name)
    user = seed_user(f"user_{username}", username, password, "自动化机器人")

    summary = {
        "project": project,
        "user": user,
        "username": username,
        "password_configured": bool(password),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if project["status"] == "failed" or user["status"] == "failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
