# -*- coding: utf-8 -*-
"""0828 知识沉淀 D1：存量工单提炼回填（薄 CLI，内核在 solution_sink）。

扫描 closed 且未沉淀（¬solution_indexed）且有信息可提
（resolution_summary 已填 或 有评论）的工单 → LLM 提炼知识卡 →
index_task_resolution 入 Qdrant（确定性 ID，幂等）→ 回写 solution_indexed 标记。

用法（连哪个库由 DATABASE_URL 决定，生产在服务器上跑）：
  HF_HUB_OFFLINE=1 python -m ai.tools.backfill_resolutions --dry-run --limit 5
  HF_HUB_OFFLINE=1 python -m ai.tools.backfill_resolutions --limit 200
"""
import argparse
import asyncio
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="只提炼打印，不入库不标记")
    ap.add_argument("--include-skipped", action="store_true",
                    help="重试之前判定空卡跳过的（solution_index_status=empty）")
    ap.add_argument("--task-id", type=int,
                    help="强制重跑单张工单，无视标记与候选条件（空卡误杀复检）")
    args = ap.parse_args()

    from ai.core.solution_sink import (
        load_candidates, process_ticket)
    candidates = load_candidates(args.limit, args.offset, args.include_skipped,
                                 args.task_id)
    print(f"待处理 {len(candidates)} 张工单"
          f"{'（dry-run）' if args.dry_run else ''}")
    if not candidates:
        return

    retriever = None
    if not args.dry_run:
        from ai.core import get_retrieval_service
        retriever = await get_retrieval_service()

    stats = {"indexed": 0, "empty": 0, "failed": 0}
    t0 = time.time()
    for i, row in enumerate(candidates, 1):
        try:
            status = await process_ticket(row, retriever, args.dry_run)
            stats[status] += 1
            # CLI 下 logger 的 INFO 会被吞（未初始化日志系统），逐张 print 进度
            print(f"  [{i}/{len(candidates)}] #{row['id']}: {status}", flush=True)
        except Exception as e:
            stats["failed"] += 1
            print(f"  [{i}/{len(candidates)}] #{row['id']}: 失败 {e}", flush=True)
    print(f"\n完成: 入库 {stats['indexed']} | 空卡 {stats['empty']} | "
          f"失败 {stats['failed']} | 耗时 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
