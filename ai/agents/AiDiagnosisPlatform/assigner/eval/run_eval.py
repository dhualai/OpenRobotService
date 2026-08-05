"""
离线评估：用历史已派单工单跑 assigner，按姓名对比 ground truth 计算命中率。

用法：
    0. 先跑 python build_dataset.py  生成 eval_dataset.json （如有 不必重复生成）
    1. 确认 AI 后端可运行（LLM / Embedding 可用）
    2. python ai/agents/AiDiagnosisPlatform/assigner/eval/run_eval.py --limit 10
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Dict, List

# ── 路径 ──
_project_root = Path(__file__).resolve().parents[5]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))
# 同时加 backend 目录（ai 需要访问 app.models 等）
_backend_dir = _project_root / "backend"
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    TicketContext, EngineerProfile, AssignmentResult,
)
from ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow import DispatchFlow
from ai.agents.AiDiagnosisPlatform.assigner.sync.engineers_sync import load_engineers
from ai.core.logging import get_logger

DATA_DIR = Path(__file__).parent / "data"
DATASET_FILE = DATA_DIR / "eval_dataset.json"

logger = get_logger("ASSIGNER_EVAL")


def load_dataset() -> List[dict]:
    with open(DATASET_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                return v
    if isinstance(data, list):
        return data
    return []


def build_ticket_context(row: dict) -> TicketContext:
    meta = row.get("metadata_info") or {}
    return TicketContext(
        id=str(row["ticket_id"]),
        title=row.get("title", ""),
        problem_description=row.get("description", ""),
        status=row.get("status", "resolved"),
        priority=row.get("priority"),
        project_name=row.get("project_name"),
        robot_type=meta.get("robot_type") or meta.get("robotType", ""),
        fault_code=meta.get("fault_code") or meta.get("faultCode", ""),
    )


async def evaluate(
    dataset: List[dict],
    engineers: List[EngineerProfile],
    limit: int = 0,
) -> dict:
    """逐条评估，按姓名比对"""
    dispatch = DispatchFlow()

    # 构建工程师名字集合，只评估指派人存在于本地库中的工单
    eng_names = {e.name for e in engineers}

    if limit:
        dataset = dataset[:limit]

    total = len(dataset)
    hits = 0
    confidence_sum = 0.0
    decision_counts: Dict[str, int] = {}
    time_sum = 0.0
    skipped = 0
    details: List[dict] = []

    for i, row in enumerate(dataset):
        gt_name = (row.get("assignee_name") or "").strip()

        if not gt_name or gt_name not in eng_names:
            skipped += 1
            continue

        ticket = build_ticket_context(row)
        t0 = time.perf_counter()
        result: AssignmentResult = await dispatch.aassign(
            ticket_context=ticket,
            engineer_profiles=engineers,
        )
        elapsed = time.perf_counter() - t0
        time_sum += elapsed

        hit = result.engineer_name.strip() == gt_name
        if hit:
            hits += 1

        confidence_sum += result.confidence_score
        decision_counts[result.decision_type] = decision_counts.get(result.decision_type, 0) + 1

        details.append({
            "ticket_id": row["ticket_id"],
            "title": row.get("title", "")[:60],
            "gt_name": gt_name,
            "predicted_name": result.engineer_name,
            "predicted_id": result.engineer_id,
            "hit": hit,
            "confidence": result.confidence_score,
            "decision": result.decision_type,
            "elapsed_ms": round(elapsed * 1000),
        })

        if (i + 1) % 10 == 0:
            print(f"  进度: {i+1}/{total}  命中: {hits}/{i+1} ({hits/(i+1):.1%})")

    valid = total - skipped
    summary = {
        "total": total,
        "valid": valid,
        "skipped": skipped,
        "hits": hits,
        "accuracy": hits / valid if valid else 0,
        "avg_confidence": confidence_sum / total if total else 0,
        "avg_time_ms": round(time_sum / total * 1000, 0) if total else 0,
        "decision_distribution": decision_counts,
        "details": details,
    }
    return summary


def print_summary(s: dict):
    print("\n" + "=" * 60)
    print("  派单离线评估结果")
    print("=" * 60)
    print(f"  工单总数:        {s['total']}")
    print(f"  可评估:          {s['valid']} (指派人存在于本地用户库)")
    print(f"  跳过:            {s['skipped']} (指派人不在本地)")
    print(f"  命中率:          {s['accuracy']:.1%} ({s['hits']}/{s['valid']})")
    print(f"  平均置信度:      {s['avg_confidence']:.1%}")
    print(f"  平均耗时:        {s['avg_time_ms']:.0f}ms")
    print(f"  决策分布:        {s['decision_distribution']}")
    print("=" * 60)

    errors = [d for d in s["details"] if not d["hit"]][:5]
    if errors:
        print(f"\n  未命中案例 ({len(s['details']) - s['hits']} 条，展示前 5):")
        for d in errors:
            print(f"    #{d['ticket_id']} [{d['title'][:40]}]")
            print(f"      GT: {d['gt_name']}  预测: {d['predicted_name']}  置信度={d['confidence']:.0%}")


async def main(limit: int = 0):
    if not DATASET_FILE.exists():
        print(f"未找到数据集: {DATASET_FILE}")
        return

    print("加载数据...")
    dataset = load_dataset()
    # 用本地 users 表（已有工程师画像）构建候选人池
    engineers = load_engineers(reload=True)
    print(f"  工单: {len(dataset)}  工程师: {len(engineers)}")

    print(f"开始评估{' (限制 ' + str(limit) + ' 条)' if limit else ''}...")
    summary = await evaluate(dataset, engineers, limit=limit)

    print_summary(summary)

    ts = time.strftime("%Y%m%d_%H%M%S")
    result_file = DATA_DIR / f"eval_result_{ts}.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {result_file}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0, help="限制评估条数（0=全部）")
    args = p.parse_args()
    asyncio.run(main(limit=args.limit))
