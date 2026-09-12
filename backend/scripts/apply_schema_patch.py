"""数据库结构补丁命令 —— 直接对库执行迁移（幂等，可重复运行）。

背景：本项目的 alembic 迁移链与存量库状态不一致（部分列由 create_all/手工
补上、链上仍记录为未应用），`alembic upgrade head` 会因 Duplicate column 报错
中断（如 users.company_id）。本命令绕过 alembic，按代码模型定义直接补齐缺失的
列/索引，效果等同对应的迁移脚本，适用于本机与部署服务器。

用法（在 backend/ 目录下，用项目 venv）：
    .venv/Scripts/python.exe scripts/apply_schema_patch.py

连接参数默认同 app/core/config.py 的 DB_CONFIG（root/123456@127.0.0.1:3306/helpdesk），
可用 DATABASE_URL 环境变量覆盖：mysql+pymysql://user:pass@host:port/db
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

# 待补齐的列（与模型定义一致）：{表: [(列名, DDL 片段, 索引名或 None)]}
PATCHES = {
    "users": [
        ("phone", "VARCHAR(20) NULL COMMENT '用户手机号（企业微信通知 @ 人用）'", "ix_users_phone"),
    ],
    "departments": [
        ("profile_text", "TEXT NULL COMMENT '部门职责描述（AI 派单部门分类用）'", None),
        ("examples", "JSON NULL COMMENT '典型工单示例（[{title, dept}]）'", None),
    ],
    "tasks": [
        # 步骤协商（step negotiation）相关列：模型见 app/models/task.py，无对应 alembic 迁移，
        # 存量库缺列时会让项目列表/任务指标查询报 1054 Unknown column
        ("step_last_updated_by", "VARCHAR(100) NULL COMMENT '最近一次改step的操作人：assigned/creator侧标识，用于判定待处理回合'", None),
        ("step_last_updated_at", "DATETIME NULL COMMENT '最近一次step更新时间'", None),
        ("step_negotiation_round", "INT NOT NULL DEFAULT 0 COMMENT '协商回合数：初始0，对手回应一次+1'", None),
        ("step_phase_round", "INT NOT NULL DEFAULT 0 COMMENT '阶段回合数：complete-step 推进+1，初始0=第一轮；0时协商节点不受sequence下限限制'", None),
        ("curr_step_agreed", "TINYINT(1) NOT NULL DEFAULT 0 COMMENT '当前协商节点是否已协商一致：respond 置 True；negotiate-step/complete-step 重置为 False'", None),
        ("escalate_count", "INT NOT NULL DEFAULT 0 COMMENT '升级上报次数：>0 表示已升级，协商回合重置为1且不再受限'", None),
    ],
    # 项目列表 GET /api/admin/projects/ 500 的根因列：模型有、存量库无，
    # 缺列时 select 报 1054 Unknown column 'project.project_manager_id'
    "project": [
        ("project_manager_id", "VARCHAR(64) NULL COMMENT '项目经理ID（与 users.id 同长度，用于关联角色）'", None),
    ],
    "task_operation_logs": [
        ("ended_at", "DATETIME NULL COMMENT '查看结束时间（仅 VIEW 有值）'", None),
        ("duration_seconds", "INT NULL COMMENT '查看时长（秒，仅 VIEW 有值）'", None),
    ],
}


def main() -> int:
    cfg = _db_config()
    conn = pymysql.connect(
        host=cfg["host"], user=cfg["user"], password=cfg["password"],
        port=cfg["port"], database=cfg["database"], charset="utf8mb4",
    )
    cur = conn.cursor()
    changed = False
    try:
        for table, patches in PATCHES.items():
            cur.execute(f"SHOW COLUMNS FROM `{table}`")
            existing_cols = {row[0] for row in cur.fetchall()}
            cur.execute(f"SHOW INDEX FROM `{table}`")
            existing_idx = {row[2] for row in cur.fetchall()}
            for col, ddl, index in patches:
                if col in existing_cols:
                    print(f"[SKIP] {table}.{col} 已存在")
                else:
                    cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{col}` {ddl}")
                    print(f"[ADD] {table}.{col} {ddl}")
                    changed = True
                if index and index not in existing_idx:
                    cur.execute(f"CREATE INDEX `{index}` ON `{table}` (`{col}`)")
                    print(f"[ADD] 索引 {index} on {table}({col})")
                    changed = True
        conn.commit()
    finally:
        conn.close()
    print("完成：数据库结构已与代码模型对齐" if changed else "完成：无缺失，无需改动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
