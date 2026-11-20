"""把 tasks.created_by 从 users.username 迁到 users.id（一次性数据修复）。

默认 dry-run，只打印映射预览；加 --execute 才写库。

    python scripts/migrate_task_created_by_user_id.py
    python scripts/migrate_task_created_by_user_id.py --execute

代码读写已双键兼容，迁库可在发版后执行。system/unknown 等非用户值保持原样。
"""

from __future__ import annotations

import argparse
import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "backend")
_cwd = os.getcwd()
os.chdir(BACKEND_DIR)
sys.path.insert(0, BACKEND_DIR)
from app.core.config import settings  # noqa: E402
os.chdir(_cwd)

import pymysql  # noqa: E402


def get_connection():
    cfg = settings.DB_CONFIG
    return pymysql.connect(
        host=cfg["host"],
        port=int(cfg["port"]),
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset="utf8mb4",
        autocommit=False,
    )


def preview(cur) -> list[tuple]:
    cur.execute(
        """
        SELECT
          t.id,
          t.created_by AS old_value,
          u.id AS new_value,
          u.name AS user_name
        FROM tasks t
        INNER JOIN users u ON t.created_by = u.username
        WHERE t.created_by IS NOT NULL
          AND t.created_by <> ''
          AND t.created_by <> u.id
        ORDER BY t.id
        """
    )
    return list(cur.fetchall())


def unmatched(cur) -> list[tuple]:
    cur.execute(
        """
        SELECT t.id, t.created_by
        FROM tasks t
        LEFT JOIN users u_name ON t.created_by = u_name.username
        LEFT JOIN users u_id ON t.created_by = u_id.id
        WHERE t.created_by IS NOT NULL
          AND t.created_by <> ''
          AND u_name.id IS NULL
          AND u_id.id IS NULL
        ORDER BY t.id
        """
    )
    return list(cur.fetchall())


def already_id(cur) -> int:
    cur.execute(
        """
        SELECT COUNT(*)
        FROM tasks t
        INNER JOIN users u ON t.created_by = u.id
        WHERE t.created_by IS NOT NULL AND t.created_by <> ''
        """
    )
    return int(cur.fetchone()[0])


def main():
    parser = argparse.ArgumentParser(description="tasks.created_by: username → users.id")
    parser.add_argument("--execute", action="store_true", help="写库；默认只预览")
    args = parser.parse_args()

    conn = get_connection()
    try:
        cur = conn.cursor()
        cfg = settings.DB_CONFIG
        print(f"数据库: {cfg['host']}:{cfg['port']}/{cfg['database']}")

        rows = preview(cur)
        leftover = unmatched(cur)
        done = already_id(cur)

        print(f"已是 users.id: {done} 条")
        print(f"待迁移 username→id: {len(rows)} 条")
        print(f"对不上任何用户（含 system/unknown，保持原值）: {len(leftover)} 条")

        if rows:
            print("\n待迁移样本:")
            for task_id, old, new, name in rows[:20]:
                print(f"  task#{task_id}: {old} → {new} ({name})")
            if len(rows) > 20:
                print(f"  ... 其余 {len(rows) - 20} 条省略")

        if leftover:
            print("\n无法映射（保持原值）:")
            for task_id, old in leftover[:30]:
                print(f"  task#{task_id}: {old}")
            if len(leftover) > 30:
                print(f"  ... 其余 {len(leftover) - 30} 条省略")

        if not args.execute:
            print("\n预览结束，未写库。确认无误后加 --execute 执行。")
            return

        if not rows:
            print("\n没有需要迁移的行，不写库。")
            return

        cur.execute(
            """
            UPDATE tasks t
            INNER JOIN users u ON t.created_by = u.username
            SET t.created_by = u.id
            WHERE t.created_by IS NOT NULL
              AND t.created_by <> ''
              AND t.created_by <> u.id
            """
        )
        conn.commit()
        print(f"\n已更新 {cur.rowcount} 行 tasks.created_by。")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
