"""时间锚点 → export_logs.sh 所需 YYYYMMDDHHMM。"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional


_DIGITS = re.compile(r"\D+")


def to_export_time_str(occurrence_time: Optional[str] = None) -> str:
    """解析工单发生时间 / 创建时间；失败则用当前本地时间。

    支持常见格式与 ISO（含 ``T``、微秒、可选时区后缀 ``Z`` / ``+08:00``）。
    """
    raw = (occurrence_time or "").strip()
    if raw:
        # ISO：先去掉时区，再把 T 换成空格，便于统一 strptime
        iso = raw
        if iso.endswith("Z"):
            iso = iso[:-1]
        if "+" in iso[10:]:
            iso = iso[: iso.rfind("+", 10)]
        elif iso.count("-") > 2 and iso.rfind("-") > 10:
            # 负时区如 -08:00
            cut = iso.rfind("-", 10)
            if ":" in iso[cut:]:
                iso = iso[:cut]
        iso = iso.replace("T", " ", 1).strip()
        # 截断微秒到秒，避免 %f 位数不定
        if "." in iso:
            head, frac = iso.split(".", 1)
            frac_digits = "".join(c for c in frac if c.isdigit())[:6]
            iso = f"{head}.{frac_digits}" if frac_digits else head

        digits = _DIGITS.sub("", raw)
        if len(digits) >= 12:
            return digits[:12]
        if len(digits) == 8 and " " not in iso and "T" not in raw.upper():
            return digits + "0000"

        for candidate in (iso, raw[:26], raw[:19]):
            for fmt in (
                "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M",
                "%Y-%m-%d",
                "%Y/%m/%d",
            ):
                try:
                    dt = datetime.strptime(candidate[:26], fmt)
                    return dt.strftime("%Y%m%d%H%M")
                except ValueError:
                    continue
    return datetime.now().strftime("%Y%m%d%H%M")
