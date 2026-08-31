"""
二次派单感知增强 —— 数据库部署脚本（幂等，可重复执行）

从 backend/.env 读取 DATABASE_URL，自动连库：
  1) 新建 task_dispatch_log 表（IF NOT EXISTS，已存在则跳过）
  2) 老库补 users.phone 列（列不存在才加；新库 create_all 已含则跳过）

不依赖 alembic 迁移链（服务器可能是多头/情况不确定，用最稳的手工 DDL 方式）。

用法（在 backend 目录）：
    uv run python deploy_redispatch.py
"""
import os
import re
import sys
from pathlib import Path

# 数据库连接：优先从环境变量，否则读 backend/.env
DATABASE_URL = os.environ.get("DATABASE_URL", "")

_ENV_FILES = [
    Path(__file__).resolve().parent / ".env",          # backend/.env
    Path(__file__).resolve().parent.parent / "backend" / ".env",
]

if not DATABASE_URL:
    for env_file in _ENV_FILES:
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    DATABASE_URL = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
            if DATABASE_URL:
                break

if not DATABASE_URL:
    print("❌ 未找到 DATABASE_URL（请确认 backend/.env 已配置）")
    sys.exit(1)


def _parse_db_url(url: str):
    """mysql+pymysql://user:pass@host:port/db?charset=... → (user, pass, host, port, db)"""
    m = re.match(r".*?://([^:]+):([^@]*)@([^:/]+):?(\d*)/([^?]+)", url)
    if not m:
        raise ValueError(f"无法解析 DATABASE_URL: {url}")
    user, pwd, host, port, db = m.groups()
    return user, pwd, host, int(port or 3306), db


user, pwd, host, port, db = _parse_db_url(DATABASE_URL)
print(f"🔌 连接数据库: {host}:{port}/{db} (user={user})")


def _has_col(cur, table, col):
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        (db, table, col))
    return cur.fetchone()[0] > 0


def _has_index(cur, table, index):
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND INDEX_NAME=%s",
        (db, table, index))
    return cur.fetchone()[0] > 0


def _add_col(cur, table, col, ddl, index_name=None, index_ddl=None):
    """幂等补列：列不存在才 ADD；可附带幂等建索引。"""
    if _has_col(cur, table, col):
        print(f"   ✅ {table}.{col} 已存在，跳过")
        return
    cur.execute(ddl)
    print(f"   ✅ {table}.{col} 已补充")
    if index_name and index_ddl and not _has_index(cur, table, index_name):
        cur.execute(index_ddl)
        print(f"   ✅ {table}.{col} 索引 {index_name} 已补充")


def main():
    import pymysql

    conn = pymysql.connect(
        host=host, port=port, user=user, password=pwd, database=db,
        charset="utf8mb4", autocommit=True,
    )
    cur = conn.cursor()

    try:
        # ── 1) 建 task_dispatch_log 表（幂等）──
        print("\n▶ 建表 task_dispatch_log ...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS `task_dispatch_log` (
              `id` bigint NOT NULL AUTO_INCREMENT COMMENT '日志ID',
              `task_id` bigint NOT NULL COMMENT '任务ID（1 工单 → N 轮派单）',
              `dispatch_round` int NOT NULL COMMENT '派单轮次（第 1 次派单=1，重派自增）',
              `preferred_id` varchar(50) DEFAULT NULL COMMENT '意向处理人 users.id',
              `assigned_id` varchar(50) NOT NULL COMMENT '实际接单人 users.id',
              `confidence` float DEFAULT NULL COMMENT '置信度（拼音命中略降 0.85）',
              `decision_type` varchar(20) DEFAULT NULL COMMENT 'auto/recommend/fallback',
              `reasoning` text COMMENT '派单理由',
              `profile` json DEFAULT NULL COMMENT '被派人画像',
              `candidates` json DEFAULT NULL COMMENT '本轮精排 Top10 快照',
              `matched_pref` tinyint(1) DEFAULT NULL COMMENT '是否派到意向人',
              `name_collision` tinyint(1) DEFAULT NULL COMMENT '是否同名命中',
              `pinyin_match` tinyint(1) DEFAULT NULL COMMENT '是否拼音命中',
              `created_at` datetime NOT NULL DEFAULT (now()) COMMENT '派单时间',
              PRIMARY KEY (`id`),
              KEY `ix_task_dispatch_log_task_id` (`task_id`),
              KEY `ix_task_dispatch_log_created_at` (`created_at`),
              KEY `ix_task_dispatch_log_id` (`id`),
              CONSTRAINT `task_dispatch_log_ibfk_1`
                FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        print("   ✅ task_dispatch_log 就绪")

        # ── 2) 老库补 users.phone 列（幂等）──
        print("\n▶ 检查 users.phone ...")
        _add_col(
            cur, "users", "phone",
            "ALTER TABLE users ADD COLUMN phone VARCHAR(20) NULL",
            index_name="ix_users_phone",
            index_ddl="ALTER TABLE users ADD INDEX ix_users_phone (phone)",
        )

        # ── 3) 老库补 tasks 任务阶段三列（同事功能字段，幂等；老库缺列会导致查工单报错）──
        print("▶ 检查 tasks 任务阶段列 ...")
        _add_col(
            cur, "tasks", "curr_step_id",
            "ALTER TABLE tasks ADD COLUMN curr_step_id BIGINT NULL",
            index_name="ix_tasks_curr_step_id",
            index_ddl="ALTER TABLE tasks ADD INDEX ix_tasks_curr_step_id (curr_step_id)",
        )
        _add_col(
            cur, "tasks", "curr_step_name",
            "ALTER TABLE tasks ADD COLUMN curr_step_name VARCHAR(128) NULL",
        )
        _add_col(
            cur, "tasks", "curr_step_endtime",
            "ALTER TABLE tasks ADD COLUMN curr_step_endtime DATETIME NULL",
        )

        print("\n🎉 部署完成！")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
