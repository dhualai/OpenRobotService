"""P2 — 解决方案验证状态回填（verified backfill）【已下沉到 core】

本文件仅保留为向后兼容的薄转发层：实现已合并到 `ai/core/verified_backfill.py`，
所有逻辑统一走 core（任务 Agent / 派单等所有 Agent 共用），避免双份漂移。

请勿在此文件新增/修改实现，直接到 `ai/core/verified_backfill.py` 维护。
"""

from __future__ import annotations

from ai.core.verified_backfill import (
    _SIGNAL,
    _detect_signal,
    backfill_verified_batch,
)

__all__ = ["_SIGNAL", "_detect_signal", "backfill_verified_batch"]


# 真实实现见 ai/core/verified_backfill.py（勿在此处重复维护）。
