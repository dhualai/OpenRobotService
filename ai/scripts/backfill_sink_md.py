# -*- coding: utf-8 -*-
"""补写历史归档报告的沉淀卡 md（一次性回填）。

背景：run_archive_ingest_worker 在**入库成功那一刻**落 md；在这个改动之前
已经入库的归档报告不会再走一遍 worker（archive_sink_status 已打标），
所以需要这个脚本把历史的补出来。

用法：
    python ai/scripts/backfill_sink_md.py --dry-run     # 只统计，不写文件
    python ai/scripts/backfill_sink_md.py               # 写文件

产物：<数据根>/kb/sink_cards/工单{id}_{point_id[:12]}.md
      与线上自动落的那批同一命名约定，重复执行幂等（同名覆盖）。
"""
import argparse
import sys
from pathlib import Path

# 直接 python ai/scripts/xxx.py 运行时，仓库根不在 sys.path 上，先补进来
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text

_ARCHIVED_APPROVED_SQL = (
    "SELECT t.id, t.title, t.description, t.assigned_to, t.metadata_info, "
    "       t.project_name, t.task_type, "
    "       r.content AS report_content, r.reviewer AS report_reviewer, "
    "       r.version AS report_version, r.reviewed_at AS report_reviewed_at "
    "FROM tasks t "
    "JOIN archive_reports r "
    "  ON r.task_id = t.id AND r.submit_status = 'approved' "
    " AND r.version = (SELECT MAX(r2.version) FROM archive_reports r2 "
    "                  WHERE r2.task_id = t.id AND r2.submit_status = 'approved') "
    "WHERE t.status = 'archived' "
    "ORDER BY t.id"
)


def main() -> int:
    ap = argparse.ArgumentParser(description="回填历史归档报告的沉淀卡 md")
    ap.add_argument("--dry-run", action="store_true", help="只统计将写多少份，不落文件")
    args = ap.parse_args()

    from ai.core.database import SessionLocal
    from ai.core.archive_flow import write_archive_sink_md

    db = SessionLocal()
    try:
        rows = [dict(r) for r in db.execute(text(_ARCHIVED_APPROVED_SQL)).mappings().all()]
    finally:
        db.close()

    if not rows:
        print("[BACKFILL] 没有已审核通过的归档报告")
        return 0

    empty = [r["id"] for r in rows if not (r.get("report_content") or "").strip()]
    todo = [r for r in rows if (r.get("report_content") or "").strip()]
    print(f"[BACKFILL] 归档报告 {len(rows)} 篇，其中正文为空 {len(empty)} 篇（跳过），"
          f"待写 {len(todo)} 篇")
    if args.dry_run:
        for r in todo[:20]:
            print(f"  - 工单 #{r['id']} {(r.get('title') or '')[:40]}")
        if len(todo) > 20:
            print(f"  … 其余 {len(todo) - 20} 篇")
        return 0

    ok = fail = 0
    for r in todo:
        try:
            path = write_archive_sink_md(r)
            ok += 1
            print(f"  [OK] #{r['id']} → {path}")
        except Exception as e:
            fail += 1
            print(f"  [ERR] #{r['id']} {e}")
    print(f"[BACKFILL] 完成：成功 {ok}，失败 {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
