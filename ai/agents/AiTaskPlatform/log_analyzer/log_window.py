"""log_window — 日志时间窗截断（针对超大日志）

有发生时间时，截取故障时刻前一段写入临时文件再建索引。
默认只看故障前 15 分钟 ``[T-15min, T]``（前因窗口）；行原文整行写入。

``before_minutes`` / ``after_minutes`` 仅作可选覆盖（例如排查瞬间事件时的
±2 分钟完整窗兜底），不改变默认策略。
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

_RE_TS = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?(?:,\d{3})?)")

# 默认：故障时刻前 15 分钟（前因窗口）
WINDOW_MINUTES = 15

_MAX_NO_TS_LINES = 200


def _parse_ts(ts_str: str) -> Optional[datetime]:
    ts = ts_str.strip()
    try:
        return datetime.strptime(ts[:16], "%Y-%m-%d %H:%M")
    except Exception:
        pass
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return None


def parse_occurred_at(raw: Optional[str]) -> Optional[datetime]:
    """解析用户提供/LLM 提取的发生时间。"""
    if not raw:
        return None
    raw = raw.strip().strip("`'\"").strip()
    m = _RE_TS.search(raw)
    ts_str = m.group(1) if m else raw
    if "-" in ts_str[:11]:
        return _parse_ts(ts_str)
    try:
        hm = ts_str[:5]
        dt = datetime.strptime(hm, "%H:%M")
        return datetime.now().replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
    except Exception:
        return None


def has_time_in_query(query: str) -> bool:
    q = query or ""
    m = _RE_TS.search(q)
    if m:
        return parse_occurred_at(m.group(1)) is not None
    if re.search(r"\d{4}-\d{1,2}-\d{1,2}[ T]\d{1,2}[:点时]\d{1,2}", q):
        return True
    if re.search(r"(?<![\d:])(\d{1,2})[:点时](\d{1,2})\b", q):
        return True
    return False


def extract_time_window(
    log_path: str,
    occurred_at: Optional[str] = None,
    window_minutes: Optional[int] = None,
    before_minutes: Optional[int] = None,
    after_minutes: Optional[int] = None,
    keep_no_ts: int = _MAX_NO_TS_LINES,
) -> str:
    """截取时间窗内的**完整行**到临时文件。

    默认 ``[T-window_minutes, T]``（只看故障前，``window_minutes`` 默认 15）。
    若显式传入 ``before_minutes`` / ``after_minutes``，则用
    ``[T-before, T+after]``（可选兜底，例如瞬间事件的 ±2 分钟完整窗）。

    Args:
        log_path: 原始日志
        occurred_at: 故障/任务创建时间
        window_minutes: 故障前窗口宽度（分钟）；未给 before/after 时生效
        before_minutes / after_minutes: 显式前后窗（可选兜底覆盖）
        keep_no_ts: 无时间戳但紧邻匹配行时最多额外保留行数

    Returns:
        临时文件路径；失败或 0 行则回退原路径
    """
    if not log_path or not os.path.exists(log_path):
        return log_path

    center = parse_occurred_at(occurred_at)
    if center is None:
        return log_path

    if before_minutes is not None or after_minutes is not None:
        # 可选覆盖：对称/非对称前后窗（兜底场景）
        before = max(0, int(before_minutes if before_minutes is not None else 0))
        after = max(0, int(after_minutes if after_minutes is not None else 0))
        lo = center - timedelta(minutes=before)
        hi = center + timedelta(minutes=after)
        win_desc = f"前{before}m~后{after}m"
    else:
        # 主路径：只看故障前 window 分钟
        span = WINDOW_MINUTES if window_minutes is None else max(1, int(window_minutes))
        lo = center - timedelta(minutes=span)
        hi = center
        win_desc = f"前{span}m"

    try:
        tmp = tempfile.NamedTemporaryFile(
            prefix="logwin_", suffix=".log", delete=False, delete_on_close=False,
        )
        tmp_path = tmp.name
        count = 0
        last_match_ts: Optional[datetime] = None
        with open(log_path, "r", encoding="utf-8", errors="replace") as f, \
                open(tmp_path, "w", encoding="utf-8") as out:
            for line in f:
                m = _RE_TS.search(line)
                if m:
                    dt = _parse_ts(m.group(1))
                    if dt is None:
                        continue
                    if lo <= dt <= hi:
                        out.write(line)
                        count += 1
                        last_match_ts = dt
                else:
                    if last_match_ts is not None and count > 0:
                        out.write(line)
        tmp.close()
        if count == 0:
            os.unlink(tmp_path) if os.path.exists(tmp_path) else None
            logger.warning(
                f"[log_window] 时间窗 {occurred_at} {win_desc} 内无匹配，回退原文件"
            )
            return log_path
        logger.info(
            f"[log_window] 截取到 {count} 行 "
            f"(窗={occurred_at} {win_desc})，"
            f"临时 {Path(tmp_path).name}"
        )
        return tmp_path
    except Exception as e:
        logger.warning(f"[log_window] 时间窗截取失败，回退原文件: {e}")
        return log_path
