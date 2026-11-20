"""日志附件稳定缓存目录管理（根治"反复讨论同一份日志"重复下载/解压/重建索引的问题）

背景：
- 原实现 `materialize_path` 每次 `tempfile.mkdtemp(prefix="log_dl_")` 解压到新临时目录、
  讨论结束即清理，导致：log_path 每次不同 → 索引缓存永远 miss，且日志文件很快被删。
- 根治：日志附件下载/解压到**按 task_id + 对象 key 稳定命名**的缓存目录，不随讨论清理，
  同一份日志每次讨论落到同一路径，索引缓存（`sub_agent._LOG_INDEX_CACHE`）真正生效。

生命周期：
- 工单处于讨论中时，日志缓存保留复用。
- 工单**已解决/已关闭**时，通过 `cleanup_task_log_cache(task_id)` 删除该工单所有日志缓存
  （含磁盘缓存目录 + 进程内内存索引），由后端在状态变更时调用 AI 清理 API 触发。

目录结构：
    <project_root>/uploads/log_cache/<task_id>/<object_key_hash>/<filename>
                                                └─ 同一对象稳定，跨讨论复用
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Optional

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

# 项目根：ai/core/log_cache.py → parent(ai/core) → parent(ai) → parent(项目根)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 日志缓存根目录：<项目根>/uploads/log_cache
LOG_CACHE_ROOT = _PROJECT_ROOT / "uploads" / "log_cache"


def _hash_key(obj_key: str) -> str:
    """把对象 key（如 usp-helpdesk/sess_x/logs.zip.localproxy）hash 成稳定短名。"""
    if not obj_key:
        return "obj"
    # 保留文件名便于排查，前缀 hash 短名避免路径过长/非法字符
    base = os.path.basename(obj_key.rstrip("/")) or "obj"
    clean = "".join(c if (c.isalnum() or c in "._-") else "_" for c in base)[:80]
    h = hashlib.md5(obj_key.encode("utf-8")).hexdigest()[:12]
    return f"{h}_{clean}"


def get_log_cache_dir(task_id: Optional[str], obj_key: str) -> Path:
    """返回某附件对象在指定工单下的稳定缓存目录（不存在则创建）。

    同一 (task_id, obj_key) 永远映射到同一目录 → 同一份日志跨讨论复用。
    """
    tid = (str(task_id) or "no_task").replace("/", "_").replace("\\", "_")
    d = LOG_CACHE_ROOT / tid / _hash_key(obj_key)
    d.mkdir(parents=True, exist_ok=True)
    return d


def cleanup_task_log_cache(task_id: Optional[str]) -> int:
    """删除某工单的所有日志缓存（磁盘目录 + 进程内内存索引缓存）。

    Args:
        task_id: 工单 ID。

    Returns:
        删除的目录数量（0 = 无缓存或已清）。
    """
    if task_id is None:
        return 0
    tid = str(task_id).replace("/", "_").replace("\\", "_")
    target = LOG_CACHE_ROOT / tid
    removed = 0
    try:
        if target.exists() and target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            removed += 1
            logger.info(f"[log_cache] 已清理工单日志缓存目录: {target}")
    except Exception as e:
        logger.warning(f"[log_cache] 清理工单日志缓存目录失败 task={task_id}: {e}")

    # 同步清掉内存索引缓存里属于该工单目录的条目（key=log_path 落在该目录下）
    try:
        from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import (
            _LOG_INDEX_CACHE,
        )
        _prefix = str(target).replace("\\", "/").rstrip("/") + "/"
        for k in list(_LOG_INDEX_CACHE.keys()):
            if str(k).replace("\\", "/").startswith(_prefix):
                _LOG_INDEX_CACHE.pop(k, None)
    except Exception as e:
        logger.warning(f"[log_cache] 清理内存索引缓存失败 task={task_id}: {e}")
    return removed


def cleanup_log_cache_root(max_age_days: Optional[int] = None, dry_run: bool = False) -> int:
    """兜底清理：删除整个日志缓存根目录（或按最后访问时间清理过期项）。

    Args:
        max_age_days: 仅删除超过该天数的任务子目录；None 表示全部清空。
        dry_run: True 只统计不删除。

    Returns:
        清理/将清理的目录数。
    """
    if not LOG_CACHE_ROOT.exists():
        return 0
    import time as _t
    now = _t.time()
    removed = 0
    for child in LOG_CACHE_ROOT.iterdir():
        if not child.is_dir():
            continue
        if max_age_days is not None:
            try:
                st = child.stat()
                if now - st.st_mtime < max_age_days * 86400:
                    continue
            except Exception:
                continue
        if not dry_run:
            shutil.rmtree(child, ignore_errors=True)
        removed += 1
    return removed
