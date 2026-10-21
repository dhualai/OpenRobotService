"""历史微信用户日活回算（一次性近似回填）。

背景：wechat_user_activity 表的日活数据由微信消息回调与 H5 登录实时埋点
写入，埋点上线前的历史日期无数据。本脚本用 tasks / task_comments 表的
创建行为近似回算历史日活：微信注册用户 users.id 即 openid
（username 形如 wechat_xxx），以「当日创建过工单 / 发过评论」近似「当日活跃」，
按 (ref_date, openid) 去重后 INSERT IGNORE 写入（已存在的真实埋点行不动）。

口径说明：这是「有工单行为」的近似日活，小于真实日活；仅用于让趋势图
有历史基线，与埋点上线后的口径不严格可比。

用法（在 backend 目录下执行）：

    python scripts/backfill_wechat_user_dau.py                     # dry-run 预览
    python scripts/backfill_wechat_user_dau.py --execute          # 实际写入
    python scripts/backfill_wechat_user_dau.py --begin 2026-09-01 --end 2026-10-20 --execute

默认日期范围：tasks 最早创建日期 ~ 昨天（今天由实时埋点负责，不回算）。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

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

SOURCE_TABLES = ["tasks", "task_comments"]
BATCH_SIZE = 500

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS wechat_user_activity (
  id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键',
  ref_date DATE NOT NULL COMMENT '活跃日期（仅年月日）',
  openid VARCHAR(128) NOT NULL COMMENT '微信用户openid',
  source VARCHAR(32) NOT NULL DEFAULT 'message' COMMENT '当日最后活跃来源',
  last_active_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '当日最后活跃时间',
  UNIQUE KEY uq_wua_ref_date_openid (ref_date, openid),
  KEY ix_wechat_user_activity_ref_date (ref_date),
  KEY ix_wechat_user_activity_openid (openid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='微信用户日活（埋点 + 回算）'
"""

_INSERT_SQL = (
    "INSERT IGNORE INTO wechat_user_activity (ref_date, openid, source) "
    "VALUES (%s, %s, 'backfill')"
)


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


def load_wechat_openids(cur) -> list[str]:
    """微信注册用户 openid 集合（users.id 即 openid，username 形如 wechat_xxx）。"""
    cur.execute(
        "SELECT id FROM users WHERE username LIKE 'wechat\\_%'"
    )
    return [row[0] for row in cur.fetchall()]


def collect_daily_rows(cur, openids: list[str], begin: date, end: date) -> list[tuple]:
    """从 tasks / task_comments 收集 (ref_date, openid) 去重行。"""
    rows: set[tuple] = set()
    for i in range(0, len(openids), BATCH_SIZE):
        chunk = openids[i:i + BATCH_SIZE]
        placeholders = ", ".join(["%s"] * len(chunk))
        for table in SOURCE_TABLES:
            sql = f"""
                SELECT DATE(created_at) AS ref_date, created_by
                FROM {table}
                WHERE created_by IN ({placeholders})
                  AND created_at IS NOT NULL
                  AND DATE(created_at) BETWEEN %s AND %s
                GROUP BY DATE(created_at), created_by
            """
            cur.execute(sql, (*chunk, begin, end))
            for ref_date, openid in cur.fetchall():
                rows.add((ref_date, openid))
    return sorted(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="历史微信用户日活回算（tasks 行为近似）")
    parser.add_argument("--begin", default="", help="开始日期 yyyy-MM-dd；默认 tasks 最早创建日期")
    parser.add_argument("--end", default="", help="结束日期 yyyy-MM-dd；默认昨天（今天由实时埋点负责）")
    parser.add_argument("--execute", action="store_true", help="实际写入；缺省 dry-run 只预览")
    args = parser.parse_args()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # 表兜底创建：埋点服务上线（create_all）前也可独立跑本脚本
            cur.execute(_CREATE_TABLE_SQL)
            conn.commit()

            openids = load_wechat_openids(cur)
            print(f"微信注册用户（username LIKE 'wechat_%'）：{len(openids)} 人")
            if not openids:
                print("未找到微信注册用户，无需回算。")
                return 0

            today = date.today()
            if args.begin:
                begin = date.fromisoformat(args.begin)
            else:
                cur.execute("SELECT MIN(DATE(created_at)) FROM tasks")
                min_day = cur.fetchone()[0]
                begin = min_day if min_day else today - timedelta(days=1)
            end = date.fromisoformat(args.end) if args.end else today - timedelta(days=1)
            if begin > end:
                print(f"[错误] begin（{begin}）晚于 end（{end}），请调整参数。")
                return 1
            print(f"回算日期范围：{begin} ~ {end}（不含今天）")

            rows = collect_daily_rows(cur, openids, begin, end)
            day_count: dict = {}
            for ref_date, _openid in rows:
                day_count[ref_date] = day_count.get(ref_date, 0) + 1
            print(f"近似日活行数（按日期去重后）：{len(rows)} 行，覆盖 {len(day_count)} 天")
            if day_count:
                peak = max(day_count.items(), key=lambda kv: kv[1])
                print(f"峰值日：{peak[0]}（{peak[1]} 人）")

            if not args.execute:
                print("\n[dry-run] 未写入。确认无误后加 --execute 执行。")
                return 0

            written = 0
            with conn.cursor() as wcur:
                for ref_date, openid in rows:
                    wcur.execute(_INSERT_SQL, (ref_date, openid))
                    written += 1
                conn.commit()
            print(f"[done] 已写入 {written} 行（INSERT IGNORE，已有真实埋点行不受影响）。")
            return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
