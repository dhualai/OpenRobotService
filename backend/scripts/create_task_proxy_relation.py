"""代他人提单关系表 task_proxy_relation 建表命令（幂等，可重复运行）。

背景：`backend/scripts/apply_schema_patch.py` 只支持「加列/加索引」列级补丁，
新建表需本脚本；结构与 `app/models/task_proxy_relation.py` 的 ORM 定义保持一致。

用法（在 backend/ 目录下，用项目 venv）：
    .venv/Scripts/python.exe scripts/create_task_proxy_relation.py
或指定库（远端演练 / 生产）：
    DATABASE_URL=mysql+pymysql://user:pass@host:port/db \
        .venv/Scripts/python.exe scripts/create_task_proxy_relation.py

连接参数默认同 app/core/config.py 的 DB_CONFIG（root/123456@127.0.0.1:3306/helpdesk）。
"""
import os
import re
import sys

import pymysql

# 与 app/core/config.py DB_CONFIG 保持一致；DATABASE_URL 可覆盖
def _db_config() -> dict:
    url = os.environ.get("DATABASE_URL")
    if url:
        m = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:]+):(\d+)/([^?]+)", url)
        if m:
            return {
                "user": m.group(1), "password": m.group(2),
                "host": m.group(3), "port": int(m.group(4)),
                "database": m.group(5),
            }
    return {"user": "root", "password": "123456", "host": "127.0.0.1",
            "port": 3306, "database": "helpdesk"}


TABLE = "task_proxy_relation"

# 与 app/models/task_proxy_relation.py 的 ORM 定义一致
DDL = f"""
CREATE TABLE IF NOT EXISTS `{TABLE}` (
  `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '关系ID',
  `task_id` BIGINT NOT NULL COMMENT '工单ID（tasks.id）',
  `agent_id` VARCHAR(50) NOT NULL COMMENT '代理人 users.id',
  `agent_username` VARCHAR(100) NULL COMMENT '代理人 username（双键兜底）',
  `principal_id` VARCHAR(50) NOT NULL COMMENT '被代理人 users.id',
  `principal_username` VARCHAR(100) NULL COMMENT '被代理人 username（双键兜底）',
  `relation_status` VARCHAR(20) NOT NULL DEFAULT 'pending' COMMENT 'pending / acknowledged / declined',
  `source` VARCHAR(20) NOT NULL DEFAULT 'manual' COMMENT '关系来源：manual / ai / admin',
  `remark` VARCHAR(500) NULL COMMENT '备注；declined 时为拒绝原因',
  `notified_at` DATETIME NULL COMMENT '首次通知被代理人时间（UTC）',
  `acked_at` DATETIME NULL COMMENT '被代理人确认跟进时间（UTC）',
  `declined_at` DATETIME NULL COMMENT '被代理人拒绝时间（UTC）',
  `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间（UTC）',
  `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间（UTC）',
  `scope` VARCHAR(20) NULL COMMENT '预留：single / delegation',
  `valid_until` DATETIME NULL COMMENT '预留：委托有效期（UTC）',
  `delegation_id` BIGINT NULL COMMENT '预留：user_delegation.id',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_task_agent_principal` (`task_id`, `agent_id`, `principal_id`),
  KEY `ix_task_proxy_relation_id` (`id`),
  KEY `ix_task_proxy_relation_task_id` (`task_id`),
  KEY `ix_task_proxy_relation_agent_id` (`agent_id`),
  KEY `ix_task_proxy_relation_principal_id` (`principal_id`),
  KEY `ix_task_proxy_relation_relation_status` (`relation_status`),
  KEY `idx_principal_status` (`principal_id`, `relation_status`),
  CONSTRAINT `fk_task_proxy_relation_task` FOREIGN KEY (`task_id`)
    REFERENCES `tasks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='代他人提单（代理提单）关系'
"""

# 结构校验：建表后必须存在的列（防「表已存在但是旧结构」）
REQUIRED_COLUMNS = {
    "id", "task_id", "agent_id", "agent_username", "principal_id",
    "principal_username", "relation_status", "source", "remark",
    "notified_at", "acked_at", "declined_at", "created_at", "updated_at",
    "scope", "valid_until", "delegation_id",
}

REQUIRED_INDEXES = {
    "uk_task_agent_principal", "idx_principal_status",
    "ix_task_proxy_relation_task_id", "ix_task_proxy_relation_principal_id",
}


def _verify(cur) -> bool:
    """结构校验：列与索引齐不齐。"""
    cur.execute(f"SHOW COLUMNS FROM `{TABLE}`")
    cols = {row[0] for row in cur.fetchall()}
    missing_cols = REQUIRED_COLUMNS - cols

    cur.execute(f"SHOW INDEX FROM `{TABLE}`")
    idx = {row[2] for row in cur.fetchall()}
    missing_idx = REQUIRED_INDEXES - idx

    if missing_cols:
        print(f"[ERR] {TABLE} 缺列：{sorted(missing_cols)}")
    if missing_idx:
        print(f"[ERR] {TABLE} 缺索引：{sorted(missing_idx)}")
    return not missing_cols and not missing_idx


def main() -> int:
    cfg = _db_config()
    conn = pymysql.connect(
        host=cfg["host"], user=cfg["user"], password=cfg["password"],
        port=cfg["port"], database=cfg["database"], charset="utf8mb4",
    )
    cur = conn.cursor()
    try:
        cur.execute(f"SHOW TABLES LIKE '{TABLE}'")
        if cur.fetchone():
            print(f"[SKIP] {TABLE} 已存在")
        else:
            cur.execute(DDL)
            print(f"[ADD] {TABLE} 创建成功")
        conn.commit()

        if not _verify(cur):
            print("完成：结构校验未通过，请人工比对 ORM 定义")
            return 1
    finally:
        conn.close()
    print("完成：代提关系表已就绪，结构校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
