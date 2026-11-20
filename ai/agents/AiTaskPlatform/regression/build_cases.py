"""从生产库抽回归用例：真实提问 + AI 当时的回答 + **可核对材料**。

用法：
    python -m ai.agents.AiTaskPlatform.regression.build_cases --limit 20
    python -m ai.agents.AiTaskPlatform.regression.build_cases --ticket 879 --limit 3

**生成物不入库**（含真实人名与工单正文），写在 `.gitignore` 挡住的路径下。

抽取纪律（都是踩出来的，改这个脚本别退回去）：
  - **同一张工单默认只取 1 条**：否则样本被一张话痨工单占满，20 条其实只有 1 个观测。
  - **材料必须早于提问**：提问之后的评论绝不进材料 —— 否则 AI 自己的回答混进证据，
    核对就变成抄答案（这是这个工具唯一不可让步的一条）。
  - **滤掉系统通知**：归档/派审/指派这类后端自动消息不是讨论。
  - **真人发言 < 2 条的工单跳过**：没有讨论就无从核对人名。
  - **作者名走生产同一套还原**（`author_display_name`，含未实名代称）：
    材料必须与模型当时看到的长得一样，否则量的是另一个东西。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterable, Optional

# 直接跑本脚本时要能 import 后端的 app.*：把仓库根与 backend/ 加进 sys.path
# （与 ai/core/conftest.py 同一套做法）。
# 注意：import app.core.database 会在导入期执行 create_all —— 只建**缺失的表**，
# 不删不改。在本地副本上跑没问题；对着生产库跑之前请先想清楚。
_ROOT = Path(__file__).resolve().parents[4]
for _p in (str(_ROOT), str(_ROOT / "backend")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ai.core.logging import get_logger  # noqa: E402

logger = get_logger("TASK_AGENT")

AI_AUTHOR = "U老师"
_DEFAULT_OUT = Path(__file__).resolve().parent / "datasets" / "real_cases.json"

# 系统通知的特征词（后端自动写进讨论区的消息，不是人在讨论）
_SYSTEM_MARKERS = (
    "开始归档工单", "状态变更为", "已指派给", "重新指派", "开始审核",
    "归档报告", "派审", "工单已关闭", "工单已解决",
)


def is_system_notice(content: str) -> bool:
    """是不是后端自动消息（不是人在讨论）。"""
    text = str(content or "").strip()
    if not text:
        return True
    return any(m in text for m in _SYSTEM_MARKERS)


def pick_qa_pairs(comments: list[dict], per_ticket: int = 1) -> list[tuple[int, int]]:
    """返回 (提问下标, 回答下标) 列表。

    comments: 按时间升序，每项 {"author", "content", "created_by"}。
    提问 = 非 AI 作者的评论且正文含 `@U`；回答 = 其后**第一条** AI 作者的评论。
    """
    pairs: list[tuple[int, int]] = []
    n = len(comments)
    for i, c in enumerate(comments):
        if (c.get("created_by") or "") == AI_AUTHOR:
            continue
        if "@U" not in str(c.get("content") or ""):
            continue
        for j in range(i + 1, n):
            if (comments[j].get("created_by") or "") == AI_AUTHOR:
                pairs.append((i, j))
                break
    return pairs[:per_ticket]


def material_before(comments: list[dict], idx: int, render) -> str:
    """提问之前那些评论渲染成的材料（**绝不包含 idx 及其后**）。

    render: 把 [{author, content}] 渲染成讨论行的函数（用生产同一个，
            作者名才会与模型当时看到的一致）。
    """
    prior = comments[:idx]
    rows = [{"author": c.get("author"), "content": c.get("content")} for c in prior
            if str(c.get("content") or "").strip() and not is_system_notice(c.get("content"))]
    return "\n".join(render(rows, per_comment=None))


def _load_ticket_rows(task_id: int) -> tuple[dict, list[dict]]:
    """读一张工单的基本信息与全部评论（作者名已按生产口径还原）。"""
    from app.models.task import Task, TaskComment
    from app.core.database import SessionLocal
    from ai.agents.AiTaskPlatform.contexts.comments import (
        _author_display_map,
        author_display_name,
    )

    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).first()
        if task is None:
            return {}, []
        name_map = _author_display_map()
        rows = db.query(TaskComment).filter(
            TaskComment.task_id == task_id
        ).order_by(TaskComment.created_at.asc()).all()
        comments = [{
            "created_by": getattr(c, "created_by", "") or "",
            "author": author_display_name(getattr(c, "created_by", None), name_map),
            "content": getattr(c, "content", "") or "",
            "created_at": getattr(c, "created_at", None),
        } for c in rows]
        base = {
            "task_id": task.id,
            "title": task.title or "",
            "description": task.description or "",
            "robot_type": (task.metadata_info or {}).get("robot_type") or "",
        }
        return base, comments
    finally:
        db.close()


def build(limit: int = 20, *, ticket_ids: Optional[Iterable[int]] = None,
          per_ticket: int = 1, since: Optional[str] = None) -> dict:
    """抽用例。返回 {"generated_at", "cases": [...], "skipped": {...}}。"""
    from app.models.task import Task, TaskComment
    from app.core.database import SessionLocal
    from ai.agents.AiTaskPlatform.contexts.comments import (
        _author_display_map,
        author_display_name,
        render_comment_lines,
    )

    # 候选工单：**直接找「有 @U 提问」的工单**，而不是「有评论的工单」。
    # 后者命中率极低（实测 120 张里只有 8 张有问答对），白扫一大圈。
    db = SessionLocal()
    try:
        q = db.query(TaskComment.task_id).distinct().filter(
            TaskComment.content.like("%@U%"),
            TaskComment.created_by != AI_AUTHOR,
        )
        if since:
            q = q.filter(TaskComment.created_at >= since)
        if ticket_ids:
            q = q.filter(TaskComment.task_id.in_([int(t) for t in ticket_ids]))
        ids = sorted({r[0] for r in q.all()}, reverse=True)[: max(limit * 3, 60)]
    finally:
        db.close()

    cases: list[dict] = []
    skipped = {"no_pair": 0, "too_few_people": 0, "no_material": 0}
    for tid in ids:
        if len(cases) >= limit:
            break
        base, comments = _load_ticket_rows(tid)
        if not comments:
            continue
        pairs = pick_qa_pairs(comments, per_ticket)
        if not pairs:
            skipped["no_pair"] += 1
            continue
        # 真人发言 < 2 条 → 没有讨论，人名的核对无从谈起
        human = {c.get("author") for c in comments
                 if (c.get("created_by") or "") != AI_AUTHOR
                 and not is_system_notice(c.get("content"))}
        if len(human) < 2:
            skipped["too_few_people"] += 1
            continue
        for qi, ai in pairs:
            material = material_before(comments, qi, render_comment_lines)
            if not material.strip():
                skipped["no_material"] += 1
                continue
            cases.append({
                "case_id": f"real-{tid}",
                "task_id": tid,
                "question": comments[qi].get("content") or "",
                "answer": comments[ai].get("content") or "",
                # 可核对材料 = 工单描述 + 提问前的讨论史（不含附件分析/检索结果/记忆）
                "material": f"{base.get('title','')}\n{base.get('description','')}\n{material}".strip(),
                "asked_at": str(comments[qi].get("created_at") or ""),
            })
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cases": cases,
        "skipped": skipped,
        # 把「人名表」与「工单号表」在**抽取时**烤进数据集：
        # 这样跑检查完全不依赖数据库，CI 里也能跑（也顺带保证基线可比 ——
        # 人名表变了会让同一份回答的 S1 结果漂移）。
        "known_names": known_names(),
        "known_ticket_ids": all_ticket_ids(),
    }


def known_names() -> list[str]:
    """users 表里的显示名（用于 S1 人名幻觉）—— 与生产同一套映射。

    清洗两道（都在 signals 那一侧，单一来源）：
      - 未实名代称「（微信用户N）」不是人名；
      - `is_plausible_person_name` 滤掉角色词与垃圾值
        （实测 users 表里真的有 `...` 和 `管理员`，不清洗会大面积误报）。
    """
    from ai.agents.AiTaskPlatform.regression.signals import is_plausible_person_name

    try:
        from ai.agents.AiTaskPlatform.contexts.comments import _author_display_map
        names = {v for v in (_author_display_map() or {}).values() if v}
        return sorted(n for n in names
                      if not n.startswith("（微信用户") and is_plausible_person_name(n))
    except Exception as e:
        logger.warning(f"[regression] 读取用户姓名失败: {e}")
        return []


def all_ticket_ids() -> list[str]:
    """全部工单 id（用于 S2 引用真实性）。"""
    from app.models.task import Task
    from app.core.database import SessionLocal

    db = SessionLocal()
    try:
        return [str(r[0]) for r in db.query(Task.id).all()]
    except Exception as e:
        logger.warning(f"[regression] 读取工单 id 失败: {e}")
        return []
    finally:
        db.close()


def main() -> int:
    p = argparse.ArgumentParser(description="抽回归用例（真实问答 + 可核对材料）")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--ticket", type=int, action="append", default=None)
    p.add_argument("--per-ticket", type=int, default=1)
    p.add_argument("--since", default=None, help="只取该时间之后的评论，如 2026-09-01")
    p.add_argument("--out", default=str(_DEFAULT_OUT))
    args = p.parse_args()

    data = build(args.limit, ticket_ids=args.ticket, per_ticket=args.per_ticket,
                 since=args.since)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"用例 {len(data['cases'])} 条 → {out}")
    print(f"跳过：{data['skipped']}")
    return 0 if data["cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
