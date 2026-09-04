"""
离线评估：用历史已派单工单跑 assigner，评估部门路由与端到端指派人命中率。

指标：
    - Dept@1：路由 primary_dept == 实际处理人部门
    - Dept@3：实际处理人部门落在 dept_scores Top-3
    - Person@1（E2E@1）：预测指派人 == ground truth
    - Person@1|Dept@1：部门判对的前提下，指派人命中率

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
from typing import Dict, List, Optional

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
    if not isinstance(meta, dict):
        meta = {}
    return TicketContext(
        id=str(row["ticket_id"]),
        title=row.get("title", ""),
        problem_description=row.get("description", ""),
        status=row.get("status", "resolved"),
        priority=row.get("priority"),
        project_name=row.get("project_name"),
        robot_type=meta.get("robot_type") or meta.get("robotType", ""),
        fault_code=meta.get("fault_code") or meta.get("faultCode", ""),
        diagnosis_hypotheses=meta.get("diagnosis_hypotheses"),
        diagnosis_ruled_out=meta.get("diagnosis_ruled_out"),
        diagnosis_collected_info=meta.get("diagnosis_collected_info"),
    )


def resolve_gt_department(
    row: dict,
    gt_name: str,
    eng_by_name: Dict[str, EngineerProfile],
) -> str:
    """Ground truth 部门：优先数据集 assignee_dept，否则工程师画像。"""
    dept = (row.get("assignee_dept") or "").strip()
    if dept:
        return dept
    eng = eng_by_name.get(gt_name)
    return (eng.department or "").strip() if eng else ""


def top_departments(dept_scores: Dict[str, float], n: int = 3) -> List[str]:
    if not dept_scores:
        return []
    ranked = sorted(dept_scores.items(), key=lambda x: x[1], reverse=True)
    return [dept for dept, _ in ranked[:n]]


async def evaluate(
    dataset: List[dict],
    engineers: List[EngineerProfile],
    limit: int = 0,
) -> dict:
    """逐条评估：部门路由 + 端到端指派人。"""
    dispatch = DispatchFlow()
    eng_by_name: Dict[str, EngineerProfile] = {e.name: e for e in engineers if e.name}
    eng_names = set(eng_by_name)

    if limit:
        dataset = dataset[:limit]

    total = len(dataset)
    hits = 0
    dept_hits = 0
    dept_at3_hits = 0
    person_given_dept_hits = 0
    person_given_dept_total = 0
    dept_evaluable = 0
    confidence_sum = 0.0
    decision_counts: Dict[str, int] = {}
    dept_mode_counts: Dict[str, int] = {}
    time_sum = 0.0
    skipped = 0
    details: List[dict] = []

    for i, row in enumerate(dataset):
        gt_name = (row.get("assignee_name") or "").strip()

        if not gt_name or gt_name not in eng_names:
            skipped += 1
            continue

        gt_dept = resolve_gt_department(row, gt_name, eng_by_name)
        ticket = build_ticket_context(row)
        t0 = time.perf_counter()
        result: AssignmentResult = await dispatch.aassign(
            ticket_context=ticket,
            engineer_profiles=engineers,
        )
        elapsed = time.perf_counter() - t0
        time_sum += elapsed

        tighten = dispatch.last_tighten
        dept_result = tighten.dept if tighten else None
        pred_dept = (dept_result.primary_dept or "").strip() if dept_result else ""
        dept_scores = dict(dept_result.dept_scores) if dept_result else {}
        dept_mode = dept_result.mode if dept_result else "unknown"

        hit = result.engineer_name.strip() == gt_name
        if hit:
            hits += 1

        dept_hit = False
        dept_at3 = False
        if gt_dept:
            dept_evaluable += 1
            dept_hit = pred_dept == gt_dept
            top3 = top_departments(dept_scores, 3)
            if pred_dept and pred_dept not in top3:
                top3 = [pred_dept] + [d for d in top3 if d != pred_dept]
            dept_at3 = gt_dept in top3[:3]
            if dept_hit:
                dept_hits += 1
            if dept_at3:
                dept_at3_hits += 1
            if dept_hit:
                person_given_dept_total += 1
                if hit:
                    person_given_dept_hits += 1

        confidence_sum += result.confidence_score
        decision_counts[result.decision_type] = decision_counts.get(result.decision_type, 0) + 1
        dept_mode_counts[dept_mode] = dept_mode_counts.get(dept_mode, 0) + 1

        details.append({
            "ticket_id": row["ticket_id"],
            "title": row.get("title", "")[:60],
            "gt_name": gt_name,
            "gt_dept": gt_dept,
            "predicted_name": result.engineer_name,
            "predicted_id": result.engineer_id,
            "predicted_dept": pred_dept,
            "dept_mode": dept_mode,
            "dept_scores": dept_scores,
            "hit": hit,
            "dept_hit": dept_hit,
            "dept_at3": dept_at3,
            "confidence": result.confidence_score,
            "decision": result.decision_type,
            "elapsed_ms": round(elapsed * 1000),
        })

        if (i + 1) % 10 == 0:
            eval_n = i + 1 - skipped
            print(
                f"  进度: {i+1}/{total}  Person@1: {hits}/{eval_n} "
                f"Dept@1: {dept_hits}/{dept_evaluable}"
            )

    valid = total - skipped
    summary = {
        "total": total,
        "valid": valid,
        "skipped": skipped,
        "hits": hits,
        "accuracy": hits / valid if valid else 0,
        "person_at_1": hits / valid if valid else 0,
        "dept_evaluable": dept_evaluable,
        "dept_hits": dept_hits,
        "dept_at_1": dept_hits / dept_evaluable if dept_evaluable else 0,
        "dept_at3_hits": dept_at3_hits,
        "dept_at_3": dept_at3_hits / dept_evaluable if dept_evaluable else 0,
        "person_given_dept_total": person_given_dept_total,
        "person_given_dept_hits": person_given_dept_hits,
        "person_at_1_given_dept_at_1": (
            person_given_dept_hits / person_given_dept_total
            if person_given_dept_total else 0
        ),
        "avg_confidence": confidence_sum / valid if valid else 0,
        "avg_time_ms": round(time_sum / valid * 1000, 0) if valid else 0,
        "decision_distribution": decision_counts,
        "dept_mode_distribution": dept_mode_counts,
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
    print(f"  ── 部门路由 ──")
    print(f"  可评部门:        {s['dept_evaluable']} (GT 有部门信息)")
    print(f"  Dept@1:          {s['dept_at_1']:.1%} ({s['dept_hits']}/{s['dept_evaluable']})")
    print(f"  Dept@3:          {s['dept_at_3']:.1%} ({s['dept_at3_hits']}/{s['dept_evaluable']})")
    print(f"  部门路由模式:    {s['dept_mode_distribution']}")
    print(f"  ── 端到端 ──")
    print(f"  Person@1:        {s['person_at_1']:.1%} ({s['hits']}/{s['valid']})")
    print(
        f"  Person@1|Dept@1: {s['person_at_1_given_dept_at_1']:.1%} "
        f"({s['person_given_dept_hits']}/{s['person_given_dept_total']})"
    )
    print(f"  平均置信度:      {s['avg_confidence']:.1%}")
    print(f"  平均耗时:        {s['avg_time_ms']:.0f}ms")
    print(f"  决策分布:        {s['decision_distribution']}")
    print("=" * 60)

    dept_errors = [d for d in s["details"] if d.get("gt_dept") and not d.get("dept_hit")][:5]
    if dept_errors:
        print(f"\n  部门路由错误 (展示前 5):")
        for d in dept_errors:
            print(f"    #{d['ticket_id']} [{d['title'][:40]}]")
            print(
                f"      GT部门: {d['gt_dept']}  预测: {d['predicted_dept'] or '-'} "
                f"mode={d['dept_mode']}"
            )

    person_errors = [d for d in s["details"] if not d["hit"]][:5]
    if person_errors:
        print(f"\n  指派人未命中 (展示前 5):")
        for d in person_errors:
            print(f"    #{d['ticket_id']} [{d['title'][:40]}]")
            print(
                f"      GT: {d['gt_name']}({d['gt_dept']})  "
                f"预测: {d['predicted_name']}({d['predicted_dept']})  "
                f"置信度={d['confidence']:.0%}"
            )


async def main(limit: int = 0):
    if not DATASET_FILE.exists():
        print(f"未找到数据集: {DATASET_FILE}")
        return

    print("加载数据...")
    dataset = load_dataset()
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
