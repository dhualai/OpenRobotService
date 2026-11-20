"""排查进行中的双队列邮箱。

steer：插入本轮。当前日志轮次和能力边界会 drain，纳入正在进行的排查。
followup：结束后跟进。当前排查不会读它，避免跟进被当成这一轮的补充。

只活在进程内存。不跨进程、不落库。
"""

from __future__ import annotations

import asyncio
from typing import Iterable

_lock = asyncio.Lock()
_boxes: dict[str, dict[str, list[str]]] = {}


def _key(task_id: str | int | None) -> str:
    return str(task_id or "").strip()


def _lane(lane: str | None) -> str:
    return "followup" if str(lane or "").strip() == "followup" else "steer"


def _empty() -> dict[str, list[str]]:
    return {"steer": [], "followup": []}


async def put(task_id: str | int | None, text: str, lane: str = "steer") -> None:
    key = _key(task_id)
    body = (text or "").strip()
    if not key or not body:
        return
    async with _lock:
        box = _boxes.setdefault(key, _empty())
        box[_lane(lane)].append(body)


async def drain(task_id: str | int | None, lane: str = "steer") -> list[str]:
    """取走指定队列。默认只取 steer，当前排查读不到 followup。"""
    key = _key(task_id)
    if not key:
        return []
    chosen = _lane(lane)
    async with _lock:
        box = _boxes.get(key)
        if not box:
            return []
        items = box.get(chosen) or []
        box[chosen] = []
        if not box.get("steer") and not box.get("followup"):
            _boxes.pop(key, None)
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
