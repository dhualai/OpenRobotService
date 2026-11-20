"""只读跑一张已有工单，把双树实验痕迹写到日志。不调用正式派单，不写数据库。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path


def _prepare_path() -> None:
    root = Path(__file__).resolve().parents[5]
    backend = root / "backend"
    for item in (str(root), str(backend)):
        if item not in sys.path:
            sys.path.insert(0, item)


def load_ticket(db, ticket_id: int) -> dict:
    from app.models.task import Task

    row = db.query(Task).filter(Task.id == ticket_id).one_or_none()
    if row is None:
        raise SystemExit(f"工单 {ticket_id} 不存在")
    meta = row.metadata_info if isinstance(row.metadata_info, dict) else {}
    return {
        "id": row.id,
        "title": row.title or "",
        "problem_description": row.description or "",
        "robot_type": meta.get("robot_type") or "",
        "project_name": row.project_name or "",
        "assigned_to": row.assigned_to or "",
    }


async def run_ids(ticket_ids: list[int]) -> None:
    _prepare_path()
    from app.core.db import SessionLocal
    from ai.core import get_llm_client
    from ai.agents.AiDiagnosisPlatform.assigner.dual_tree.algorithm import DualTreeExperiment
    from ai.agents.AiDiagnosisPlatform.assigner.dual_tree.catalog import load_catalog

    db = SessionLocal()
    try:
        catalog = load_catalog(db)
        tickets = [load_ticket(db, ticket_id) for ticket_id in ticket_ids]
    finally:
        db.close()

    llm = await get_llm_client()
    experiment = DualTreeExperiment(catalog)
    log_dir = Path(__file__).resolve().parents[5] / "ai" / "logs" / "assigner"
    log_dir.mkdir(parents=True, exist_ok=True)
    for ticket in tickets:
        result = await experiment.run(ticket, llm)
        header = (
            f"工单 {ticket['id']} {ticket['title']}\n"
            f"项目 {ticket['project_name'] or '无'} 车型 {ticket['robot_type'] or '无'}\n"
            f"当前处理人 {ticket['assigned_to'] or '无'}\n"
            "本文件是双树实验，没有写回工单，也没有写入派单日志。\n\n"
        )
        text = header + result.render()
        path = log_dir / f"dual_tree_{ticket['id']}.log"
        path.write_text(text, encoding="utf-8")
        print(json.dumps({"ticket": ticket["id"], "log": str(path), "people": len(result.people)}, ensure_ascii=False))


def main() -> None:
    _prepare_path()
    raw = sys.argv[1:] or ["934"]
    ids = [int(item) for item in raw]
    asyncio.run(run_ids(ids))


if __name__ == "__main__":
    main()
