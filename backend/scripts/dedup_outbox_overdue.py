"""一次性清理 Outbox 中逾期提醒的重复 pending 记录。

背景：修复前逾期提醒 (notify_type=6) 把 occurrence=dayN 带进了幂等键，
导致 DAG 每天命中都会创建一张新 outbox 记录，但它们的 scheduled_at
永远相同（deadline 后第一个工作时段）。需要把同 (task_id, recipient_id,
scheduled_at) 的多条 pending 记录合并成一条。

用法：
    cd backend && python -m scripts.dedup_outbox_overdue [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict

# 确保 backend 包可导入
sys.path.insert(0, ".")

from app.core.database import db_manager
from app.models.notification import NotificationOutbox


def dedup(dry_run: bool = False) -> None:
    db = db_manager.get_db()
    try:
        # 取出所有逾期提醒的 pending/sending 记录
        rows = (
            db.query(NotificationOutbox)
            .filter(
                NotificationOutbox.notify_type == 6,
                NotificationOutbox.status.in_(["pending", "sending"]),
            )
            .all()
        )

        # 按 (task_id, recipient_id, scheduled_at) 分组
        groups: dict[tuple, list[NotificationOutbox]] = defaultdict(list)
        for r in rows:
            key = (r.task_id, r.recipient_id, r.scheduled_at)
            groups[key].append(r)

        dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
        print(f"共 {len(rows)} 条 pending/sending 逾期记录，"
              f"其中 {len(dup_groups)} 组存在重复。")

        cancelled_total = 0
        kept_total = 0

        for key, records in dup_groups.items():
            records.sort(key=lambda r: r.id)  # 按 id 字典序（≈ 创建时间）
            keep = records[0]
            discard = records[1:]
            kept_total += 1

            print(f"\n[组] task={key[0]} recipient={key[1]} "
                  f"scheduled_at={key[2]}")
            print(f"  保留: {keep.id} (status={keep.status} "
                  f"key={keep.idempotency_key})")

            for r in discard:
                print(f"  取消: {r.id} (status={r.status} "
                      f"key={r.idempotency_key})")
                if not dry_run:
                    db.query(NotificationOutbox).filter(
                        NotificationOutbox.id == r.id
                    ).update({
                        "status": "cancelled",
                        "last_error": "dedup: 同 scheduled_at 重复，已合并到 "
                                      + keep.id,
                    })
                    cancelled_total += 1

        if dup_groups:
            if dry_run:
                print(f"\n[DRY-RUN] 共 {len(dup_groups)} 组重复，"
                      f"应取消 {cancelled_total} 条，保留 {kept_total} 条")
            else:
                db.commit()
                print(f"\n完成！已取消 {cancelled_total} 条重复记录，"
                      f"保留 {kept_total} 组各一条。")
        else:
            print("\n没有需要清理的重复记录 ✅")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印，不实际修改")
    args = parser.parse_args()
    dedup(dry_run=args.dry_run)
