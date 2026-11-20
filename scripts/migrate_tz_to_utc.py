"""时区老数据迁移脚本（一次性使用）。

背景
----
后端已在 ``app/core/db.py`` 注册 connect 事件，强制每个连接会话时区为 UTC（``SET time_zone='+00:00'``），
使 ``func.now()`` 在本地/生产一致返回 UTC。但生产 MySQL 系统时区原为 ``Asia/Shanghai``(+8)，
存量 naive ``DateTime`` 列存的是 +8 wall time，前端 ``parseUtcDate`` 补 ``Z`` 当 UTC 解析会多 8 小时。

本脚本把生产存量 DateTime 列的值统一减 8 小时，转为真正的 UTC，与强制会话 UTC 后的新数据对齐。

使用
----
    # 预览（不写库）：打印每张表受影响行数 + 3 条样本前后对比
    python scripts/migrate_tz_to_utc.py --dry-run

    # 执行迁移（需二次确认）
    python scripts/migrate_tz_to_utc.py --execute

时序要求
--------
**必须在部署强制会话 UTC 的代码（db.py 改动）之前、停服状态下执行**：
  1. 停应用服务（避免迁移期间有新写入）
  2. 执行本脚本（存量数据 -8h → 全部变成 UTC）
  3. 部署 db.py 强制 UTC 代码并启动服务（新写入也是 UTC）

本地无需迁移（本地 MySQL 本就是 UTC）。脚本会自动检测并提示。

幂等保护
--------
脚本在库中建 ``_tz_migration_sentinel`` 表记录迁移时间，已迁移过则拒绝再次执行，防止 -16h。
"""

import argparse
import sys
import os
from datetime import datetime

# Windows 控制台 UTF-8 输出（避免中文/emoji 编码报错）
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 让脚本能 import backend 配置
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

import pymysql
from app.core.config import settings


# 需迁移的表 → DateTime 列清单（生产 +8 环境下存量值均需 -8h）
# 依据 backend/app/models/* 与 ai/core/database.py 的列定义，已排除 String(30) 型伪时间列
MIGRATION_TARGETS: dict[str, list[str]] = {
    "tasks": ["created_at", "updated_at", "resolved_at", "canceled_at", "closed_at", "deadline_at"],
    "task_comments": ["created_at", "updated_at"],
    "task_comment_read": ["updated_at"],
    "task_user_mapping": ["created_at", "updated_at"],
    "task_operation_logs": ["ended_at", "created_at"],
    "tickets": ["planned_at", "created_at", "updated_at"],
    "conversations": ["created_at", "updated_at"],
    "messages": ["created_at"],
    "resources": ["created_at", "updated_at", "deleted_at", "accessed_at"],
    "resource_folders": ["created_at", "updated_at", "deleted_at", "accessed_at"],
    "companies": ["created_at", "approved_at"],
    "departments": ["created_at", "approved_at"],
}

SENTINEL_TABLE = "_tz_migration_sentinel"


def get_connection():
    cfg = settings.DB_CONFIG
    return pymysql.connect(
        host=cfg["host"], port=int(cfg["port"]),
        user=cfg["user"], password=cfg["password"],
        database=cfg["database"], charset="utf8mb4",
    )


def check_session_tz(cur) -> dict:
    """检测当前 MySQL 实际时区，用 NOW() vs UTC_TIMESTAMP() 判断是否 +8。"""
    cur.execute("SELECT @@global.time_zone, @@session.time_zone, NOW(), UTC_TIMESTAMP()")
    g_tz, s_tz, now_val, utc_val = cur.fetchone()
    is_plus8 = now_val != utc_val  # 不等说明系统时区非 UTC（生产 +8）
    return {
        "global_tz": g_tz, "session_tz": s_tz,
        "now": now_val, "utc": utc_val,
        "is_utc": not is_plus8,
    }


def sentinel_exists(cur) -> bool:
    cur.execute(f"SHOW TABLES LIKE '{SENTINEL_TABLE}'")
    return cur.fetchone() is not None


def print_banner(title: str):
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def dry_run(cur):
    """预览每张表受影响行数 + 样本前后对比。"""
    print_banner("DRY RUN — 迁移预览（不写库）")
    total_rows = 0
    for table, cols in MIGRATION_TARGETS.items():
        cur.execute(f"SHOW TABLES LIKE '{table}'")
        if not cur.fetchone():
            print(f"\n[{table}] 表不存在，跳过")
            continue
        for col in cols:
            try:
                cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE '{col}'")
                if not cur.fetchone():
                    print(f"  [{table}.{col}] 列不存在，跳过")
                    continue
                cur.execute(f"SELECT COUNT(*) FROM `{table}` WHERE `{col}` IS NOT NULL")
                cnt = cur.fetchone()[0]
                total_rows += cnt
                tag = f"[{table}.{col}] 非NULL行数={cnt}"
                # 取最多 3 条样本
                cur.execute(f"SELECT id, `{col}` FROM `{table}` WHERE `{col}` IS NOT NULL ORDER BY id LIMIT 3")
                samples = cur.fetchall()
                if samples:
                    sample_str = "; ".join(
                        f"id={r[0]}: {r[1]} → DATE_SUB({r[1]}, INTERVAL 8 HOUR)" for r in samples
                    )
                    print(f"  {tag}\n      样本: {sample_str}")
                else:
                    print(f"  {tag} (无样本数据)")
            except pymysql.Error as e:
                print(f"  [{table}.{col}] 查询失败: {e}")
    print(f"\n合计需更新行数（含同列多列累加）: {total_rows}")
    print("\n如确认无误，请用 --execute 执行迁移。")


def execute(cur, conn):
    """实际执行迁移，带 sentinel 幂等保护与二次确认。"""
    if sentinel_exists(cur):
        cur.execute(f"SELECT migrated_at FROM `{SENTINEL_TABLE}` ORDER BY migrated_at DESC LIMIT 1")
        row = cur.fetchone()
        print_banner("中止：已迁移过！")
        print(f"检测到 {SENTINEL_TABLE} 已有记录，迁移时间: {row[0] if row else '(空)'}")
        print("为防止重复执行导致 -16h，拒绝再次迁移。")
        print("如确需重跑，请先手动 DROP TABLE _tz_migration_sentinel。")
        return

    # 二次确认
    print_banner("EXECUTE — 即将迁移（写库）")
    print(f"将对 {len(MIGRATION_TARGETS)} 张表的 DateTime 列执行 DATE_SUB(col, INTERVAL 8 HOUR)。")
    print("这会永久修改数据，请确保：")
    print("  1. 应用服务已停止（避免迁移期间新写入）")
    print("  2. 已对数据库做备份")
    print("  3. 当前环境确为生产 +8 MySQL（本地 UTC 无需迁移）")
    confirm = input("\n输入 YES 确认执行: ").strip()
    if confirm != "YES":
        print("已取消。")
        return

    # 创建 sentinel 表
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS `{SENTINEL_TABLE}` (
            id INT AUTO_INCREMENT PRIMARY KEY,
            migrated_at DATETIME NOT NULL,
            note VARCHAR(255)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    # 先写入哨兵（未完成标记）
    cur.execute(f"INSERT INTO `{SENTINEL_TABLE}` (migrated_at, note) VALUES (NOW(), 'in-progress')")
    sentinel_id = cur.lastrowid
    conn.commit()

    total_updated = 0
    errors = []
    for table, cols in MIGRATION_TARGETS.items():
        cur.execute(f"SHOW TABLES LIKE '{table}'")
        if not cur.fetchone():
            print(f"[{table}] 表不存在，跳过")
            continue
        print(f"\n[{table}]")
        for col in cols:
            try:
                cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE '{col}'")
                if not cur.fetchone():
                    print(f"  {col}: 列不存在，跳过")
                    continue
                sql = f"UPDATE `{table}` SET `{col}` = DATE_SUB(`{col}`, INTERVAL 8 HOUR) WHERE `{col}` IS NOT NULL"
                cur.execute(sql)
                affected = cur.rowcount
                total_updated += affected
                print(f"  {col}: 更新 {affected} 行")
            except pymysql.Error as e:
                errors.append(f"{table}.{col}: {e}")
                print(f"  {col}: 失败 - {e}")
                conn.rollback()
                return

    # 更新哨兵为完成
    cur.execute(f"UPDATE `{SENTINEL_TABLE}` SET note='done' WHERE id=%s", (sentinel_id,))
    conn.commit()

    print_banner("迁移完成")
    print(f"总计更新行数: {total_updated}")
    if errors:
        print(f"错误 {len(errors)} 条:")
        for e in errors:
            print(f"  - {e}")
    print(f"\n哨兵记录: {SENTINEL_TABLE}#{sentinel_id}")
    print("现在可以部署 db.py 强制 UTC 代码并启动服务。")


def main():
    parser = argparse.ArgumentParser(description="时区老数据迁移：存量 DateTime 列 -8h → UTC")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="预览影响行数与样本，不写库")
    group.add_argument("--execute", action="store_true", help="执行迁移（写库）")
    args = parser.parse_args()

    conn = get_connection()
    try:
        cur = conn.cursor()
        tz_info = check_session_tz(cur)
        print(f"数据库: {settings.DB_CONFIG['database']} @ {settings.DB_CONFIG['host']}:{settings.DB_CONFIG['port']}")
        print(f"global.time_zone={tz_info['global_tz']}  session.time_zone={tz_info['session_tz']}")
        print(f"NOW()={tz_info['now']}  UTC_TIMESTAMP()={tz_info['utc']}  {'(相等→UTC环境)' if tz_info['is_utc'] else '(不等→+8环境，需迁移)'}")

        # UTC 环境提示：本地无需迁移
        if tz_info["is_utc"] and not args.execute:
            print("\n[!] 当前为 UTC 环境，存量数据应已是 UTC，无需迁移。")
            print("    本地环境请直接退出；生产环境请确认是否连错了库。")
            if args.dry_run:
                print("    (dry-run 继续，仅预览)")
        elif not tz_info["is_utc"]:
            print("\n[OK] 检测到 +8 环境，存量数据需要 -8h 迁移。")

        if args.dry_run:
            dry_run(cur)
        else:
            execute(cur, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
