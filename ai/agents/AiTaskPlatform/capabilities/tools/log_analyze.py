"""LogAnalyzeCapability — 日志分析能力（第一个真实能力，F1 落地）

把现有 `LogSubAgent` 的领域逻辑包装为一个 `BaseCapability` 子类，
让 Supervisor 可调度。产品无关：内部自动通过 `product_registry` 选产品手册（多产品）。

对应设计（见 TASK_AGENT_TARGET_ARCH.md §6c）：
  - F1 = A：日志多轮推理由本能力内部（LogSubAgent）承担；上层多轮编排由 Supervisor 负责
  - 产品无关内核：本能力不写死某个产品，靠 `pick_manual_dir(log_path)` 选对应产品手册
  - 多产品：调度 UPS（当前大头） / 车端 / 服务号 / 未来其它 ORS 产品

入口：
  - 调用方（discuss/diagnose 流程）把日志路径 + 问题传进来
  - run(log_path=..., query=...) → 内部调 LogSubAgent 分析 → 返回 CapabilityResult
  - 若 runtime 注入 log_paths（USP 多服务包），首文件 fallback/空结论时自动试下一份
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult

logger = get_logger("TASK_AGENT")

# 首文件弱结论时最多再试几份（含首份一共）
_MAX_LOG_TRIES = 3

_WEAK_MARKERS = (
    "未能精确定位",
    "未发现可用错误",
    "无法定位",
    "判不了",
    "没有明确结论",
    "休息/充电",
    "TASK-MANAGER心跳",
    "仅含TASK-MANAGER",
    "无明确结论",
)


class LogAnalyzeCapability(BaseCapability):
    """日志分析能力：内部调 LogSubAgent 做领域内多轮推理（上层多轮编排由 Supervisor 负责）。"""

    name = "log_analyze"
    description = (
        "日志分析：分析 AGV/USP 车端或平台日志，定位故障根因。适用于有日志附件、"
        "需从日志查错误码/异常/时序的问题。\n"
        "重要：有故障/任务时间时，默认只截取该时刻**前 15 分钟**（前因窗口）再分析；"
        "window_minutes 可调。before/after_minutes 仅作可选兜底（如瞬间事件的 ±2 分钟完整窗）。\n"
        "输入: log_path + query + (可选)occurred_at + (可选)window/before/after_minutes + (可选)log_paths。"
        "输出: 日志分析结论。"
    )
    tags = ["log", "日志", "日志分析"]
    max_usage_per_session = 3

    def is_available(self) -> bool:
        return True

    async def run(self, **kwargs) -> CapabilityResult:
        """执行日志分析；USP 多文件时弱结论自动换下一份再试。"""
        query = kwargs.get("query") or kwargs.get("user_question") or ""
        task_context = kwargs.get("task_context") or self._default_context(query)
        occurred_at = kwargs.get("occurred_at") or kwargs.get("event_time")
        # 主路径：故障前 window_minutes（默认 15）；before/after 仅显式传入时作兜底覆盖
        before_minutes, after_minutes, window_minutes, use_ba = self._resolve_window(kwargs)

        candidates = self._candidate_paths(kwargs)
        if not candidates:
            return CapabilityResult.failure("日志分析需要 log_path 参数")

        from ai.agents.AiTaskPlatform.log_analyzer.log_window import (
            extract_time_window, has_time_in_query, parse_occurred_at,
        )

        occurred_ts = occurred_at
        if not occurred_ts:
            from ai.agents.AiTaskPlatform.log_analyzer.log_window import _RE_TS
            m = _RE_TS.search(query or "")
            if m:
                occurred_ts = m.group(1)
        if not occurred_ts and has_time_in_query(query):
            occurred_ts = occurred_at

        try:
            from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import LogSubAgent
        except Exception as e:
            return CapabilityResult.failure(f"日志分析模块加载失败: {type(e).__name__}: {e}")

        current = kwargs.get("current_task") or {}
        if isinstance(task_context, dict) and not task_context.get("task_id") and current.get("task_id"):
            task_context = {**task_context, "task_id": current.get("task_id")}
        bag = kwargs.get("round_supplements")
        if not isinstance(bag, list):
            bag = []
            kwargs["round_supplements"] = bag
        orig_progress = kwargs.get("progress_emitter")
        bus = kwargs.get("trace_bus")

        def _progress(payload: dict) -> None:
            if bus is not None:
                try:
                    desc = (payload or {}).get("description") or (payload or {}).get("id") or ""
                    if desc:
                        bus.add_event(
                            str(desc),
                            id=(payload or {}).get("id"),
                            status=(payload or {}).get("status"),
                        )
                except Exception:
                    pass
            if orig_progress is not None:
                orig_progress(payload)

        last_fail: Optional[CapabilityResult] = None
        tried_names: List[str] = []

        for attempt, raw_path in enumerate(candidates[:_MAX_LOG_TRIES]):
            name = Path(raw_path).name
            tried_names.append(name)
            analyze_path = raw_path
            window_applied = False
            tmp_path: Optional[str] = None
            no_time_applied = False

            if occurred_ts and parse_occurred_at(occurred_ts) is not None:
                if use_ba:
                    tmp_path = extract_time_window(
                        raw_path,
                        occurred_at=occurred_ts,
                        before_minutes=before_minutes,
                        after_minutes=after_minutes,
                    )
                    win_log = f"{occurred_ts} 前{before_minutes}m~后{after_minutes}m（兜底窗）"
                else:
                    tmp_path = extract_time_window(
                        raw_path,
                        occurred_at=occurred_ts,
                        window_minutes=window_minutes,
                    )
                    win_log = f"{occurred_ts} 前{window_minutes}m"
                if tmp_path and tmp_path != raw_path:
                    window_applied = True
                    analyze_path = tmp_path
                    logger.info(f"[log_analyze] 时间窗分析 {name}：{win_log}")
            else:
                try:
                    size_mb = Path(raw_path).stat().st_size / (1024 * 1024)
                except Exception:
                    size_mb = 0
                no_time_applied = True
                logger.info(
                    f"[log_analyze] 无发生时间，全量分析 {name}（{size_mb:.0f}MB）"
                )

            if attempt > 0:
                logger.info(
                    f"[log_analyze] 上一份结论偏弱，改试文件 {attempt + 1}/{_MAX_LOG_TRIES}: {name}"
                )
                _progress({
                    "id": f"log_retry_{attempt}",
                    "description": f"换日志文件再分析：{name}",
                    "status": "in_progress",
                    "capability": "log_analyze",
                    "phase": "running",
                })

            try:
                sub = LogSubAgent(analyze_path)
                result = await sub.analyze(
                    task_context=task_context,
                    user_question=query,
                    progress=_progress,
                    is_cancelled=kwargs.get("is_cancelled"),
                    task_id=str(current.get("task_id") or (task_context or {}).get("task_id") or ""),
                    supplements_bag=bag,
                )
            except Exception as e:
                logger.error(f"LogAnalyzeCapability 执行失败 file={name}: {e}")
                last_fail = CapabilityResult.failure(
                    f"日志分析失败({name}): {type(e).__name__}: {e}"
                )
                self._cleanup_tmp(tmp_path, analyze_path)
                continue

            weak = self._is_weak_result(result)
            if not result or not getattr(result, "conclusion", ""):
                last_fail = CapabilityResult(
                    text="（日志分析未得到明确结论）",
                    meta={
                        "conclusion": "",
                        "evidence": [],
                        "queries": getattr(result, "queries_made", 0) if result else 0,
                        "tried_files": tried_names,
                    },
                    ok=False,
                    error="日志分析无明确结论",
                )
                self._cleanup_tmp(tmp_path, analyze_path)
                continue

            if weak and attempt + 1 < min(len(candidates), _MAX_LOG_TRIES):
                logger.info(
                    f"[log_analyze] {name} 结论偏弱(fallback="
                    f"{getattr(result, 'fallback_used', False)})，将试下一份"
                )
                self._cleanup_tmp(tmp_path, analyze_path)
                continue

            meta = {
                "conclusion": result.conclusion,
                "evidence": getattr(result, "evidence", [])[:10],
                "queries": getattr(result, "queries_made", 0),
                "fallback": getattr(result, "fallback_used", False),
                "product": self._detect_product(raw_path),
                "window_applied": window_applied,
                "no_time_applied": no_time_applied,
                "occurred_at": occurred_ts if window_applied else None,
                "window_minutes": window_minutes if window_applied and not use_ba else None,
                "before_minutes": before_minutes if window_applied and use_ba else None,
                "after_minutes": after_minutes if window_applied and use_ba else None,
                "log_file": name,
                "tried_files": tried_names,
            }
            text = f"{result.conclusion}\n\n{result.to_prompt_text()}"
            if window_applied:
                if use_ba:
                    text += (
                        f"\n\n（分析时间窗兜底：{occurred_ts} 前{before_minutes}分钟"
                        f"~后{after_minutes}分钟）"
                    )
                else:
                    text += (
                        f"\n\n（分析时间窗：{occurred_ts} 前{window_minutes}分钟）"
                    )
            if no_time_applied and not window_applied:
                text += (
                    "\n\n（提示：若告知故障发生的大致时间，可用时间窗加速定位，速度更快。）"
                )
            if len(tried_names) > 1:
                text += f"\n\n（已依次分析：{' → '.join(tried_names)}）"
            cap = CapabilityResult(text=text, meta=meta, ok=True)
            if bus is not None:
                try:
                    bus.set_attribute("window_applied", window_applied)
                    bus.set_attribute("queries", meta.get("queries") or 0)
                    bus.set_attribute("log_file", name)
                except Exception:
                    pass
            self._cleanup_tmp(tmp_path, analyze_path)
            return cap

        return last_fail or CapabilityResult.failure("日志分析未得到明确结论")

    # ── 辅助 ──

    @staticmethod
    def _resolve_window(kwargs: dict) -> tuple:
        """返回 (before, after, window_minutes, use_ba)。

        主路径：window_minutes 默认 15（故障前窗）。
        仅当显式传入 before_minutes / after_minutes 时启用前后窗兜底。
        """
        def _as_int(v, default):
            try:
                if v is None:
                    return default
                return max(0, int(v))
            except (TypeError, ValueError):
                return default

        use_ba = "before_minutes" in kwargs or "after_minutes" in kwargs
        if use_ba:
            return (
                _as_int(kwargs.get("before_minutes"), 0),
                _as_int(kwargs.get("after_minutes"), 0),
                15,
                True,
            )
        span = _as_int(kwargs.get("window_minutes"), 15) or 15
        return 0, 0, span, False

    @staticmethod
    def _candidate_paths(kwargs: dict) -> List[str]:
        """合并 log_path + log_paths，去重保序；跳过不存在的文件。"""
        out: List[str] = []
        seen = set()
        primary = kwargs.get("log_path") or kwargs.get("path") or ""
        extras = kwargs.get("log_paths") or []
        if not isinstance(extras, (list, tuple)):
            extras = []
        for p in [primary, *extras]:
            if not p or not isinstance(p, str):
                continue
            key = os.path.normcase(os.path.abspath(p))
            if key in seen:
                continue
            if not os.path.isfile(p):
                continue
            seen.add(key)
            out.append(p)
        return out

    @staticmethod
    def _is_weak_result(result) -> bool:
        if not result:
            return True
        if getattr(result, "fallback_used", False):
            return True
        conclusion = (getattr(result, "conclusion", None) or "").strip()
        if not conclusion:
            return True
        if any(m in conclusion for m in _WEAK_MARKERS):
            return True
        # 几乎无证据且自称找不到
        evidence = getattr(result, "evidence", None) or []
        if len(evidence) < 3 and any(
            x in conclusion for x in ("无法", "没有", "未找到", "不含", "无任")
        ):
            return True
        return False

    @staticmethod
    def _cleanup_tmp(tmp_path: Optional[str], analyze_path: str) -> None:
        if tmp_path and tmp_path != analyze_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    @staticmethod
    def _default_context(query: str) -> dict:
        return {
            "title": "",
            "description": query,
            "problem_summary": query,
            "hypotheses": [],
            "ruled_out": [],
            "robot_type": "",
            "fault_code": "",
            "collected_info": {},
        }

    @staticmethod
    def _detect_product(log_path: str) -> str:
        try:
            from ai.agents.AiTaskPlatform.product_registry import pick_manual_dir
            d = pick_manual_dir(log_path)
            if d:
                return d
        except Exception:
            pass
        return "unknown"
