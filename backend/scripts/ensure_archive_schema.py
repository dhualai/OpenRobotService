"""幂等补齐「工单归档」相关库结构（9726d417 未带迁移）。

补齐内容：
  1) tasks.archived_by / tasks.archived_at
  2) tasks.status 枚举追加 ARCHIVING / ARCHIVED（只追加到末尾；纠正错误小写）
  3) task_operation_logs.operation_type 追加 ARCHIVE_* 四值（纠正错误小写）
  4) 新建 archive_reports 表（若不存在）

用法（仓库根或 backend/，用项目 venv）：
    .venv/Scripts/python.exe backend/scripts/ensure_archive_schema.py

连接：环境变量 DATABASE_URL 优先；否则读 backend/.env 的 DATABASE_URL；
再否则用默认 root/123456@127.0.0.1:3306/helpdesk。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pymysql

_BACKEND = Path(__file__).resolve().parents[1]


def _load_env_file() -> None:
    env_path = _BACKEND / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key != "DATABASE_URL":
            continue
        if key in os.environ and os.environ.get(key):
            return
        os.environ[key] = val.strip().strip('"').strip("'")
        return


def _db_config() -> dict:
    _load_env_file()
    url = os.environ.get("DATABASE_URL")
    if url:
        m = re.match(r"mysql\+pymysql://([^:]+):([^@]+)@([^:]+):(\d+)/([^?]+)", url)
        if m:
            return {
                "user": m.group(1),
                "password": m.group(2),
                "host": m.group(3),
                "port": int(m.group(4)),
                "database": m.group(5),
            }
    return {
        "user": "root",
        "password": "123456",
        "host": "127.0.0.1",
        "port": 3306,
        "database": "helpdesk",
    }


def _parse_enum(type_str: str) -> list[str]:
    # enum('a','b') → ['a','b']
    m = re.match(r"enum\((.*)\)", type_str or "", re.I)
    if not m:
        return []
    return re.findall(r"'((?:\\'|[^'])*)'", m.group(1))


def _enum_modify_sql(
    table: str,
    column: str,
    values: list[str],
    null_ok: bool,
    default,
) -> str:
    enum_sql = ",".join("'" + v.replace("'", "\\'") + "'" for v in values)
    null_sql = "NULL" if null_ok else "NOT NULL"
    default_sql = ""
    if default is not None:
        default_sql = f" DEFAULT '{str(default).replace(chr(39), chr(92) + chr(39))}'"
    return (
        f"ALTER TABLE `{table}` MODIFY COLUMN `{column}` "
        f"ENUM({enum_sql}) {null_sql}{default_sql}"
    )


def _ensure_enum_values(cur, table: str, column: str, needed: list[str]) -> bool:
    """补齐 ENUM，并纠正「同义不同大小写」成员。

    存量库与 SQLAlchemy SQLEnum 默认都按 Python enum **name** 落库
    （CREATE / ARCHIVE_START）。若先前误加了 archive_start，MySQL 在
    大小写不敏感校对下会把 ARCHIVE_START 存成 archive_start，读回时
    SQLAlchemy LookupError。必须把成员与行数据统一成 needed 中的规范写法。
    """
    cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
    row = cur.fetchone()
    if not row:
        print(f"[WARN] {table}.{column} 不存在，跳过枚举补齐")
        return False
    # row: Field, Type, Null, Key, Default, Extra
    col_type = row[1]
    null_ok = (row[2] or "").upper() == "YES"
    default = row[4]
    existing = _parse_enum(col_type)
    if not existing:
        print(f"[SKIP] {table}.{column} 不是 ENUM（type={col_type}）")
        return False

    canon_by_lower = {v.lower(): v for v in needed}
    # 已有成员里大小写与规范不一致的
    wrong_case = [
        v for v in existing
        if v.lower() in canon_by_lower and v != canon_by_lower[v.lower()]
    ]
    existing_l = {v.lower() for v in existing}
    missing = [v for v in needed if v.lower() not in existing_l]

    if not missing and not wrong_case:
        print(f"[SKIP] {table}.{column} 枚举已包含 {needed}")
        return False

    if wrong_case:
        # MySQL 无法直接把 archive_start 改成 ARCHIVE_START（校对不敏感会冲突），
        # 先改 VARCHAR → 改写行数据 → 再写回规范 ENUM。
        print(f"[FIX] {table}.{column} 纠正枚举大小写: {wrong_case} → 规范名")
        null_sql = "NULL" if null_ok else "NOT NULL"
        default_sql = ""
        if default is not None:
            default_sql = f" DEFAULT '{str(default).replace(chr(39), chr(92) + chr(39))}'"
        cur.execute(
            f"ALTER TABLE `{table}` MODIFY COLUMN `{column}` "
            f"VARCHAR(64) {null_sql}{default_sql}"
        )
        for old in wrong_case:
            new = canon_by_lower[old.lower()]
            cur.execute(
                f"UPDATE `{table}` SET `{column}`=%s WHERE `{column}`=%s",
                (new, old),
            )
            print(f"[FIX] {table}.{column} 行数据 {old!r} → {new!r}")
        # 重建成员列表：非冲突保留，冲突用规范名，再追加缺失
        rebuilt: list[str] = []
        seen_l: set[str] = set()
        for v in existing:
            canon = canon_by_lower.get(v.lower(), v)
            if canon.lower() in seen_l:
                continue
            rebuilt.append(canon)
            seen_l.add(canon.lower())
        for v in needed:
            if v.lower() not in seen_l:
                rebuilt.append(v)
                seen_l.add(v.lower())
        cur.execute(_enum_modify_sql(table, column, rebuilt, null_ok, default))
        print(f"[FIX] {table}.{column} ENUM → {rebuilt}")
        return True

    new_vals = existing + missing
    cur.execute(_enum_modify_sql(table, column, new_vals, null_ok, default))
    print(f"[ADD] {table}.{column} 枚举追加 {missing}")
    return True


def _ensure_column(cur, table: str, column: str, ddl: str, index: str | None = None) -> bool:
    cur.execute(f"SHOW COLUMNS FROM `{table}`")
    cols = {r[0] for r in cur.fetchall()}
    changed = False
    if column in cols:
        print(f"[SKIP] {table}.{column} 已存在")
    else:
        cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {ddl}")
        print(f"[ADD] {table}.{column}")
        changed = True
    if index:
        cur.execute(f"SHOW INDEX FROM `{table}`")
        idxs = {r[2] for r in cur.fetchall()}
        if index not in idxs:
            cur.execute(f"CREATE INDEX `{index}` ON `{table}` (`{column}`)")
            print(f"[ADD] 索引 {index}")
            changed = True
        else:
            print(f"[SKIP] 索引 {index} 已存在")
    return changed


def _ensure_archive_reports(cur) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.tables "
        "WHERE table_schema = DATABASE() AND table_name = 'archive_reports'"
    )
    if cur.fetchone()[0]:
        print("[SKIP] 表 archive_reports 已存在")
        return False
    cur.execute(
        """
        CREATE TABLE `archive_reports` (
          `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '报告ID',
          `task_id` BIGINT NOT NULL COMMENT '任务ID',
          `content` LONGTEXT NOT NULL COMMENT '归档报告正文（markdown）',
          `version` INT NOT NULL DEFAULT 1 COMMENT '版本号',
          `created_by` VARCHAR(50) NOT NULL COMMENT '创建人 users.id',
          `updated_by` VARCHAR(50) NULL COMMENT '最近编辑者ID',
          `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
          `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
          `submit_status` VARCHAR(20) NOT NULL DEFAULT 'draft' COMMENT 'draft/submitted/approved/rejected',
          `reviewer` VARCHAR(50) NULL COMMENT '审核人 users.id',
          `reviewed_at` DATETIME NULL COMMENT '审核时间',
          `review_comment` TEXT NULL COMMENT '驳回理由',
          `revision` INT NOT NULL DEFAULT 1 COMMENT '乐观锁',
          PRIMARY KEY (`id`),
          KEY `ix_archive_reports_task_id` (`task_id`),
          KEY `ix_archive_reports_created_by` (`created_by`),
          KEY `ix_archive_reports_submit_status` (`submit_status`),
          KEY `ix_archive_reports_reviewer` (`reviewer`),
          CONSTRAINT `archive_reports_ibfk_1`
            FOREIGN KEY (`task_id`) REFERENCES `tasks` (`id`) ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='工单归档报告'
        """
    )
    print("[ADD] 表 archive_reports")
    return True


def main() -> int:
    cfg = _db_config()
    print(f"连接 {cfg['host']}:{cfg['port']}/{cfg['database']} …")
    try:
        conn = pymysql.connect(
            host=cfg["host"],
            user=cfg["user"],
            password=cfg["password"],
            port=cfg["port"],
            database=cfg["database"],
            charset="utf8mb4",
        )
    except Exception as e:
        print(f"[错误] 无法连接数据库: {e}")
        return 1

    changed = False
    cur = conn.cursor()
    try:
        changed |= _ensure_column(
            cur,
            "tasks",
            "archived_by",
            "VARCHAR(50) NULL COMMENT '归档审核人ID（users.id）'",
            "ix_tasks_archived_by",
        )
        changed |= _ensure_column(
            cur,
            "tasks",
            "archived_at",
            "DATETIME NULL COMMENT '归档完成时间'",
            None,
        )
        # 与存量 CREATE/CLOSED、SQLAlchemy SQLEnum(name) 一致，必须用大写 name。
        changed |= _ensure_enum_values(
            cur, "tasks", "status", ["ARCHIVING", "ARCHIVED"]
        )
        changed |= _ensure_enum_values(
            cur,
            "task_operation_logs",
            "operation_type",
            [
                "ARCHIVE_START",
                "ARCHIVE_REPORT_SUBMIT",
                "ARCHIVE_APPROVE",
                "ARCHIVE_REJECT",
            ],
        )
        changed |= _ensure_archive_reports(cur)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[错误] {e}")
        return 1
    finally:
        conn.close()

    print("完成：归档库结构已就绪" if changed else "完成：无需改动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
