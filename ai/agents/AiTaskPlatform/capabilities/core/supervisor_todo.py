"""TodoList — 排查 Agent 自我任务清单（产品无关通用内核能力）

对标 Claude Code 的 todo 机制：Agent 拿到任务后自列 todo，边执行边更新（新增/勾选/调整）。

设计约定（见 TASK_AGENT_TARGET_ARCH.md §6c.8）：
  - TodoItem: 平铺结构（初判足够，后续需要再做子任务层级）
  - 只存活在单次排查会话的进程内存中，请求结束由 Supervisor 释放
  - 产品无关：不绑定工单/产品，既用于工单诊断也用于未来 ORS 自身问题分析
  - 通过 to_prompt()/progress() 暴露给 LLM 与前端（呼应 G6 透明化）

用法：
    todo = TodoList()
    item = todo.add("分析日志错误 XNA-169")
    todo.mark_in_progress(item.id)
    ...
    todo.mark_done(item.id, result="已确认根因")
    print(todo.to_prompt())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TodoItem:
    """单个待办项（平铺）。"""
    id: str                    # 自增 id："1" / "2" / ...
    description: str           # 待办描述（LLM/前端展示）
    status: str = "pending"    # pending | in_progress | completed
    capability: str = ""       # 关联能力名（可选，供 Supervisor 调度）
    result_summary: str = ""   # 完成后的简短结果（供透明化 G6）


class TodoList:
    """Agent 自我任务清单：创建/更新/勾选/进度查询。"""

    def __init__(self) -> None:
        self._items: list[TodoItem] = []
        self._counter: int = 0

    # ── 写操作 ──
    def add(self, description: str, capability: str = "") -> TodoItem:
        """新增一个 pending 待办项，返回它。"""
        self._counter += 1
        item = TodoItem(
            id=str(self._counter),
            description=description,
            status="pending",
            capability=capability,
        )
        self._items.append(item)
        return item

    def update(self, todo_id: str, **fields) -> Optional[TodoItem]:
        """按 id 更新待办项的任意字段，返回更新后的项（不存在返回 None）。"""
        item = self._find(todo_id)
        if item is None:
            return None
        for k, v in fields.items():
            if hasattr(item, k):
                setattr(item, k, v)
        return item

    def mark_in_progress(self, todo_id: str) -> None:
        """标记某个待办为进行中。"""
        self.update(todo_id, status="in_progress")

    def mark_done(self, todo_id: str, result: str = "") -> None:
        """标记某个待办完成，并记录简短结果。"""
        self.update(todo_id, status="completed", result_summary=result)

    # ── 读操作 ──
    def next_pending(self) -> Optional[TodoItem]:
        """取下一个未开始的待办（按顺序首个 pending）。"""
        for it in self._items:
            if it.status == "pending":
                return it
        return None

    def progress(self) -> tuple[int, int]:
        """返回 (已完成数, 总数)。"""
        done = sum(1 for it in self._items if it.status == "completed")
        return done, len(self._items)

    def all_done(self) -> bool:
        """是否全部完成（无 pending/in_progress）。"""
        return all(it.status == "completed" for it in self._items)

    def to_prompt(self) -> str:
        """序列化成给 LLM 看的 todo 文本（供 Supervisor 每轮注入）。"""
        if not self._items:
            return "（暂无 todo）"
        lines = []
        for it in self._items:
            mark = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(it.status, "[ ]")
            extra = f"  ← {it.result_summary}" if it.result_summary else ""
            lines.append(f"{it.id}. {mark} {it.description}{extra}")
        return "\n".join(lines)

    def to_dict_list(self) -> list[dict]:
        """转为 dict 列表（供前端/tracing 展示）。"""
        return [
            {
                "id": it.id,
                "description": it.description,
                "status": it.status,
                "capability": it.capability,
                "result_summary": it.result_summary,
            }
            for it in self._items
        ]

    # ── 内部 ──
    def _find(self, todo_id: str) -> Optional[TodoItem]:
        for it in self._items:
            if it.id == todo_id:
                return it
        return None
