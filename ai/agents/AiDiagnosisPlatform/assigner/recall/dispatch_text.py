"""A 路入库 / 检索共用的工单向量文本。

固定四栏，缺栏写「无」，描述截到 300 字。入库和查询必须同一套。
"""

from __future__ import annotations

from typing import Optional

_DESC_MAX = 300
_EMPTY = "无"


def _slot(value: Optional[str], *, limit: Optional[int] = None) -> str:
    text = (value or "").strip()
    if not text:
        return _EMPTY
    if limit is not None:
        text = text[:limit]
    return text


def build_dispatch_ticket_text(
    title: Optional[str] = None,
    description: Optional[str] = None,
    robot_type: Optional[str] = None,
    fault_code: Optional[str] = None,
    skip_empty: bool = False,
) -> str:
    """拼成固定四栏，供 embed。

    skip_empty=True 时不写空的车型/故障码栏（B 路用）。缺栏一律写「无」会让
    无关工单共享「车型：无 / 故障码：无」，把余弦抬高、簇糊成一团。
    A 路入库/检索保持 skip_empty=False，与已有 Qdrant 向量对齐。
    """
    lines = [
        f"标题：{_slot(title)}",
        f"描述：{_slot(description, limit=_DESC_MAX)}",
    ]
    robot = (robot_type or "").strip()
    fault = (fault_code or "").strip()
    if robot or not skip_empty:
        lines.append(f"车型：{_slot(robot_type)}")
    if fault or not skip_empty:
        lines.append(f"故障码：{_slot(fault_code)}")
    return "\n".join(lines)
