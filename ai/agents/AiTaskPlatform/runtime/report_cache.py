"""「帮我分析」诊断报告的进程内快照。

diagnose 每次现算报告、**不落库**（有意如此：报告是那一轮的结论，入库会带来过期数据
与污染）。但讨论区用 @# 引用一个工单时，用户常问"他上次那份分析报告说了啥"——那份报告
只存在于刚才那次调用的响应里，事后谁也拿不到。所以这里留一份短时快照，让同一进程内
后续的 @# 引用能拿到。

只在进程内存：不跨进程、不落库、重启即失。ai/run.py 起的是单进程 uvicorn，所以没有
多 worker 共享问题。**因此 get 拿不到是常态而不是异常**，调用方必须如实告诉用户
"报告不落库，只能重新点一次『帮我分析』生成"，不许编造内容。
"""

from __future__ import annotations

import time
from typing import Optional

TTL_SECONDS = 6 * 3600  # 一个班次内有效，再久也该重算
MAX_ENTRIES = 200  # 进程内最多留多少份（超出淘汰最早写入的）
MAX_CHARS = 20000  # 单份留存上限，防内存膨胀

_snapshots: dict[str, tuple[float, str]] = {}  # task_id -> (写入时间, 报告正文)


def _key(task_id) -> str:
    return str(task_id or "").strip()


def put(task_id, text: str) -> None:
    """存一份报告。空 id / 空正文直接忽略（不占位，避免 get 拿到空串）。"""
    key = _key(task_id)
    body = (text or "").strip()
    if not key or not body:
        return
    now = time.time()
    _prune(now)
    _snapshots.pop(key, None)  # 重新写入排到队尾，淘汰时优先丢旧的
    _snapshots[key] = (now, body[:MAX_CHARS])
    while len(_snapshots) > MAX_ENTRIES:
        _snapshots.pop(next(iter(_snapshots)), None)


def get(task_id) -> Optional[str]:
    """取报告正文；没有（没生成过 / 已过期 / 重启过）返回 None。"""
    key = _key(task_id)
    if not key:
        return None
    _prune(time.time())
    hit = _snapshots.get(key)
    return hit[1] if hit else None


def clear() -> None:
    """清空（测试用）。"""
    _snapshots.clear()


def _prune(now: float) -> None:
    for key in [k for k, (ts, _) in _snapshots.items() if now - ts > TTL_SECONDS]:
        _snapshots.pop(key, None)
