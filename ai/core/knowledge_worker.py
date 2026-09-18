# -*- coding: utf-8 -*-
"""知识沉淀 Worker：扫描已解决工单 → LLM 提炼知识卡 → 写入 Qdrant。

0901 移入 ai/core（共享核心）：知识沉淀跨平台（数据来自工单平台、检索
服务诊断平台），由 ai/run.py lifespan 统一挂载（start_knowledge_worker），
不属于任何单个 agent 的私有 services。

与 ai/tools/backfill_resolutions.py（存量回填 CLI）共享内核
ai.core.solution_sink：DB 组装 → 提炼 → 确定性 ID 入库（幂等）→ 标记回写。

v1（搁置版）的三个硬伤：没用 metadata_info.resolution_summary（正则刮评论
猜解法，最后一条人类评论很可能是「好的谢谢」）、没有解决人、没有回填模式。
"""
import asyncio

from ai.config import get_ai_config
from ai.core.logging import get_logger

logger = get_logger("KNOWLEDGE_SINK")

_FAIL_RETRY_LIMIT = 3  # 同一工单连续失败次数上限，超过跳过（不堵队列）


async def run_knowledge_worker(stop_event: asyncio.Event):
    """后台知识沉淀 worker：扫描已解决工单 → 提炼 → 写入 Qdrant。

    失败重试：进程内存计数，连续 _FAIL_RETRY_LIMIT 次失败的工单本进程内
    不再重试（重启后重新计数——宕机重试无害，索引写入幂等）。
    """
    from ai.core import get_retrieval_service
    from ai.core import solution_sink

    config = get_ai_config()
    interval = config.diagnosis_scan_interval
    logger.info(f"Knowledge worker v2 started (scan interval={interval}s)")

    retriever = None
    fail_counts: dict[int, int] = {}
    rounds = 0

    while not stop_event.is_set():
        try:
            candidates = [
                row for row in solution_sink.load_candidates(limit=20)
                if fail_counts.get(row["id"], 0) < _FAIL_RETRY_LIMIT
            ]
            if candidates:
                logger.info(f"[knowledge] 待沉淀 {len(candidates)} 张工单")
                if retriever is None:
                    retriever = await get_retrieval_service()
                for row in candidates:
                    if stop_event.is_set():
                        break
                    tid = row["id"]
                    try:
                        status = await solution_sink.process_ticket(row, retriever)
                        fail_counts.pop(tid, None)
                        logger.info(f"[knowledge] #{tid}: {status}")
                    except Exception as e:
                        fail_counts[tid] = fail_counts.get(tid, 0) + 1
                        logger.warning(f"[knowledge] #{tid} 失败"
                                       f"({fail_counts[tid]}/{_FAIL_RETRY_LIMIT}): {e}")
            else:
                # 心跳日志（INFO）：空候选原本只有 debug 级日志，生产上看
                # 不到——「没日志」无法区分 worker 挂了还是没活干。首轮必打
                # 一条，之后每 15 轮（默认约 15 分钟）报一次平安。
                if rounds % 15 == 0:
                    logger.info("[knowledge] 心跳：worker 运行中，当前无待沉淀工单")
                rounds += 1
        except Exception as e:
            logger.warning(f"[knowledge] 扫描失败: {e}")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    logger.info("Knowledge worker stopped")


def start_knowledge_worker() -> tuple[asyncio.Task, asyncio.Event]:
    """启动知识沉淀 worker，返回 (task, stop_event) 供 lifespan shutdown 使用。"""
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_knowledge_worker(stop_event))
    return task, stop_event
