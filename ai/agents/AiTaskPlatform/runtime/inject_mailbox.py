"""本轮强插邮箱：分析进行中工程师「插入本轮」的文字，按 task_id 排队给当前排查读。

只活在进程内存，请求结束后未消费的条目会留到该工单下一次 drain
（下一次 discuss/diagnose 边界会再读）。不跨进程、不落库。
"""

from __future__ import annotations

import asyncio
from typing import Iterable

_lock = asyncio.Lock()
_boxes: dict[str, list[str]] = {}


def _key(task_id: str | int | None) -> str:
    return str(task_id or "").strip()


async def put(task_id: str | int | None, text: str) -> None:
    key = _key(task_id)
    body = (text or "").strip()
    if not key or not body:
        return
    async with _lock:
        _boxes.setdefault(key, []).append(body)


async def drain(task_id: str | int | None) -> list[str]:
    key = _key(task_id)
    if not key:
        return []
    async with _lock:
        items = _boxes.pop(key, [])
    return [x for x in items if x]


def format_block(texts: Iterable[str]) -> str:
    """拼进最终 discuss prompt 的「工程师本轮补充」段。"""
    parts: list[str] = []
    seen: set[str] = set()
    for raw in texts:
        text = (raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        parts.append(f"- {text}")
    if not parts:
        return ""
    return (
        "## 工程师本轮补充\n"
        + "\n".join(parts)
        + "\n请把以上补充纳入本轮分析与答复，不要当成下一轮新问题。"
    )
