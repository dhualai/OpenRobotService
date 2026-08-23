"""log_window — 日志时间窗截断（针对 512MB 大日志的优化）

背景（用户需求，2026-08-14）：日志一般有 512MB，不应该全量建索引/读取。
更优做法：先让用户提供"故障发生时间点"，只截取该时间点**前**一段时间窗（默认 15 分钟）内的
日志行到临时小文件，再在临时文件上建索引分析。这样：
  - 处理量从 512MB 骤降到几分钟内的少量行
  - 聚焦故障发生前的前因窗口，排查更准
  - 建索引更快、内存占用低

时间窗截取策略：
  - 只看故障时刻**前** window 分钟的日志（[T-window, T]），而非前后各一段：
    故障日志的价值在于"故障发生前发生了什么导致它"，故障时刻之后的数据通常已无关。
  - window_minutes 是**默认值（15），Agent/调度可根据故障类型、日志密度自行调节**
    （如慢问题可能要 60min，日志很稀疏可能要 30min）。
  - 流式逐行读取（不把全文件载入内存）
  - 用正则提取每行时间戳，落在 [T-window, T] 内的行写入临时文件
  - 无时间戳且紧跟匹配行的行会保留（避免丢关键上下文前因）

用法：
    from ai.agents.AiTaskPlatform.log_analyzer.log_window import (
        extract_time_window, has_time_in_query, WINDOW_MINUTES
    )
    # 默认 15 分钟
    sub_path = extract_time_window(log_path, occurred_at="2026-08-14 14:32")
    # Agent 调节时间窗（如要更长的前因）
    sub_path = extract_time_window(log_path, occurred_at="2026-08-14 14:32", window_minutes=30)
    # 返回临时文件路径（已截取）；失败/无时间则返回原路径
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

# 时间戳格式：YYYY-MM-DD HH:MM[:SS][,mmm]
_RE_TS = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?::\d{2})?(?:,\d{3})?)")

# 默认时间窗（分钟）
WINDOW_MINUTES = 15

# 无时间戳行最多保留多少行（通常是头部/最近行，避免丢上下文）
_MAX_NO_TS_LINES = 200


def _parse_ts(ts_str: str) -> Optional[datetime]:
    """把日志时间戳字符串解析成 datetime。支持 YYYY-MM-DD HH:MM / HH:MM:SS / ,mmm。"""
    ts = ts_str.strip()
    fmt = "%Y-%m-%d %H:%M"
    try:
        return datetime.strptime(ts[:16], fmt)
    except Exception:
        pass
    try:
        return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return None


def parse_occurred_at(raw: Optional[str]) -> Optional[datetime]:
    """解析用户提供/LLM 提取的发生时间。返回 datetime 或 None。

    支持格式：
      - '2026-08-14 14:32' / '2026-08-14 14:32:05' / '2026-08-14 14:32:05,123'
      - 仅 '14:32'（缺日期时，用今日日期补齐）
    """
    if not raw:
        return None
    raw = raw.strip().strip("`'\"").strip()
    m = _RE_TS.search(raw)
    ts_str = m.group(1) if m else raw
    # 尝试带日期
    if "-" in ts_str[:11]:
        return _parse_ts(ts_str)
    # 仅时分：HH:MM 或 HH:MM:SS → 用今天
    try:
        hm = ts_str[:5]
        dt = datetime.strptime(hm, "%H:%M")
        return datetime.now().replace(hour=dt.hour, minute=dt.minute, second=0, microsecond=0)
    except Exception:
        return None


def has_time_in_query(query: str) -> bool:
    """判断 query/描述里是否含一个可识别的时间点。"""
    q = query or ""
    m = _RE_TS.search(q)
    if m:
        return parse_occurred_at(m.group(1)) is not None
    # 带日期的 HH:MM 或裸 HH:MM
    if re.search(r"\d{4}-\d{1,2}-\d{1,2}[ T]\d{1,2}[:点时]\d{1,2}", q):
        return True
    if re.search(r"(?<![\d:])(\d{1,2})[:点时](\d{1,2})\b", q):
        return True
    return False


def extract_time_window(
    log_path: str,
    occurred_at: Optional[str] = None,
    window_minutes: int = WINDOW_MINUTES,
    keep_no_ts: int = _MAX_NO_TS_LINES,
) -> str:
    """把 log_path 里落在 [occurred_at-window, occurred_at] 的行写入临时文件（故障发生前的窗口）。

    Args:
        log_path: 原始日志路径
        occurred_at: 故障发生时间（可解析字符串）；None 或解析失败 → 返回原路径（不做截取）
        window_minutes: 时间窗宽度（分钟，默认 15）。只看故障时刻**前**这么长的一段日志
            （[T-window, T]），由 Agent 可调（不同故障类型/日志密度可能需要更长或更短）。
        keep_no_ts: 无时间戳但紧邻的行最多保留数（保留紧邻前因上下文）

    Returns:
        - 截取成功 → 临时小文件路径（新文件，需调用方决定是否清理）
        - 无时间/解析失败 → 返回原 log_path（调方自行处理，如提示用户补时间）
        - 截取到 0 行 → 返回原 log_path（时间窗无匹配，保留原日志避免误判）
    """
    if not log_path or not os.path.exists(log_path):
        return log_path

    center = parse_occurred_at(occurred_at)
    if center is None:
        return log_path  # 无有效时间 → 不截取（由上层提示用户给时间）

    # 只看故障时刻**前** window 分钟：[T-window, T]
    win = timedelta(minutes=window_minutes)
    lo, hi = center - win, center

    try:
        tmp = tempfile.NamedTemporaryFile(prefix="logwin_", suffix=".log", delete=False, delete_on_close=False)
        tmp_path = tmp.name
        count = 0
        no_ts = 0
        keep_head: list[str] = []
        last_match_ts: Optional[datetime] = None
        with open(log_path, "r", encoding="utf-8", errors="replace") as f, open(tmp_path, "w", encoding="utf-8") as out:
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
                    # 无时间戳行：若紧跟匹配行则保留（上下文关键行），否则忽略
                    if last_match_ts is not None:
                        out.write(line)
                        no_ts += 1
        tmp.close()
        if count == 0:
            # 时间窗内无匹配 → 用原日志（避免截空导致分析没数据）
            os.unlink(tmp_path) if os.path.exists(tmp_path) else None
            logger.warning(f"[log_window] 时间窗 {occurred_at} 前{window_minutes}m 内无匹配日志，回退原文件")
            return log_path
        logger.info(f"[log_window] 截取到 {count} 行 (窗={occurred_at}前{window_minutes}m)，临时={Path(tmp_path).name}")
        return tmp_path
    except Exception as e:
        logger.warning(f"[log_window] 时间窗截取失败，回退原文件: {e}")
        return log_path
