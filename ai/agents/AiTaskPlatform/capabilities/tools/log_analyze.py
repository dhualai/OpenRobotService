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

用法：
    cap = LogAnalyzeCapability()
    result = await cap(log_path="/path/to/log.log", query="为什么车不动")
    # result 是 CapabilityResult（text=日志分析结论，meta={conclusion, evidence, queries}）
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.capabilities.core.base import BaseCapability, CapabilityResult
from ai.agents.AiTaskPlatform.capabilities.core.supervisor import MAX_ROUNDS_PER_TASK

logger = get_logger("TASK_AGENT")


class LogAnalyzeCapability(BaseCapability):
    """日志分析能力：内部调 LogSubAgent 做领域内多轮推理（上层多轮编排由 Supervisor 负责）。

    属性（BaseCapability 元数据）：
      - name: log_analyze
      - description: 给调度 LLM 看的能力描述（ACI）
      - tags: ["log", "日志"]
      - max_usage_per_session: 限制单会话内日志分析次数（防过度调度）
    """

    name = "log_analyze"
    description = (
        "日志分析：分析 AGV/USP 车端或平台日志，定位故障根因。适用于有日志附件、"
        "需从日志查错误码/异常/时序的问题。\n"
        "重要：大日志（>50MB）最好先让用户提供故障发生的**大致时间**（occurred_at），"
        "只分析故障前一段时间窗（默认 window_minutes=15，可按故障类型调节，慢问题可加大）。\n"
        "若无发生时间且日志很大，返回 need_time 提示向用户询问时间。\n"
        "输入: log_path + query + (可选)occurred_at + (可选)window_minutes。输出: 日志分析结论。"
    )
    tags = ["log", "日志", "日志分析"]
    max_usage_per_session = 3  # 单会话最多分析 3 次（护栏，防过度调度）

    def is_available(self) -> bool:
        """日志分析能力始终可用（只要配置了日志分析环境）。

        与 code_skill 不同：日志分析不需要服务器放代码，只要日志文件可达即可。
        用 BaseCapability 默认 True；如需按环境禁用，可在未来覆写。
        """
        return True

    async def run(self, **kwargs) -> CapabilityResult:
        """执行日志分析。

        入参（kwargs，由 Supervisor._dispatch 传 query，或调用方直接传）：
          - log_path: 日志文件路径（必填）
          - query: 要排查的问题（可选段）
          - task_context: 任务上下文 dict（可选，供 LogSubAgent 使用；缺失时用最少信息）
          - occurred_at / event_time: 故障发生时间（可选）。提供后只截取该时间前的窗口加速；
            缺失时**仍全量分析**（保证功能可用），仅在结果里提示给时间可加速。
        """
        log_path = kwargs.get("log_path") or kwargs.get("path")
        query = kwargs.get("query") or kwargs.get("user_question") or ""
        task_context = kwargs.get("task_context") or self._default_context(query)
        occurred_at = kwargs.get("occurred_at") or kwargs.get("event_time")
        # 时间窗宽度（故障发生前多少分钟），Agent/调度可传入调节；默认 15
        try:
            window_minutes = int(kwargs.get("window_minutes", 15))
        except (TypeError, ValueError):
            window_minutes = 15

        if not log_path:
            return CapabilityResult.failure("日志分析需要 log_path 参数")

        # ── 时间窗判定：用户提供发生时间 → 截取小窗口加速；没有 → 全量分析（保证可用）──
        window_applied = False
        tmp_path: Optional[str] = None
        no_time_applied = False
        from ai.agents.AiTaskPlatform.log_analyzer.log_window import (
            extract_time_window, has_time_in_query, parse_occurred_at,
        )

        # 1) 从显式的 occurred_at 或 query 里解析时间
        occurred_ts = occurred_at
        if not occurred_ts:
            m = None
            # 尝试从 query 提取时间表述
            from ai.agents.AiTaskPlatform.log_analyzer.log_window import _RE_TS
            m = _RE_TS.search(query or "")
            if m:
                occurred_ts = m.group(1)
        if not occurred_ts and has_time_in_query(query):
            occurred_ts = occurred_at  # 保留为 None，下面统一判断

        if occurred_ts and parse_occurred_at(occurred_ts) is not None:
            # 2) 有时间 → 截取故障发生前的那个时间窗临时文件，只分析窗口
            tmp_path = extract_time_window(
                log_path, occurred_at=occurred_ts, window_minutes=window_minutes
            )
            if tmp_path and tmp_path != log_path:
                window_applied = True
                log_path = tmp_path  # 用截取后的临时小文件建索引
        else:
            # 3) 无时间 → 不做时间窗，**回退全量分析原日志**（保证功能始终可用）。
            #    时间窗只是优化/加速，不是必须；大日志全量分析只是更慢/占资源。
            #    在结果里标注 hint：建议用户提供时间可加速定位（供上层提示，不阻塞分析）。
            try:
                size_mb = Path(log_path).stat().st_size / (1024 * 1024)
            except Exception:
                size_mb = 0
            no_time_applied = True
            logger.info(f"[log_analyze] 无发生时间，全量分析（{size_mb:.0f}MB）；给时间可加速窗口截取")

        # 懒加载 LogSubAgent（避免模块加载副作用；日志多轮推理由它承担）
        try:
            from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import LogSubAgent
        except Exception as e:
            return CapabilityResult.failure(f"日志分析模块加载失败: {type(e).__name__}: {e}")

        try:
            sub = LogSubAgent(log_path)
            # progress：由上层（discuss_flow via runtime_ctx）注入的 ai.progress 回调，
            # 让 LogSubAgent 内部各阶段（建索引/R1..Rn）也能上报子节点，避免前端只看一个卡住的节点
            result = await sub.analyze(
                task_context=task_context,
                user_question=query,
                progress=kwargs.get("progress_emitter"),
            )
        except Exception as e:
            logger.error(f"LogAnalyzeCapability 执行失败: {e}")
            return CapabilityResult.failure(f"日志分析失败: {type(e).__name__}: {e}")

        if not result or not getattr(result, "conclusion", ""):
            return CapabilityResult(
                text="（日志分析未得到明确结论）",
                meta={
                    "conclusion": "",
                    "evidence": [],
                    "queries": getattr(result, "queries_made", 0),
                },
                ok=False,
                error="日志分析无明确结论",
            )

        # 组装返回（LogAnalysisResult → CapabilityResult）
        meta = {
            "conclusion": result.conclusion,
            "evidence": getattr(result, "evidence", [])[:10],
            "queries": getattr(result, "queries_made", 0),
            "fallback": getattr(result, "fallback_used", False),
            "product": self._detect_product(log_path),
            "window_applied": window_applied,
            "no_time_applied": no_time_applied,
            "occurred_at": occurred_ts if window_applied else None,
            "window_minutes": window_minutes if window_applied else None,
        }
        text = f"{result.conclusion}\n\n{result.to_prompt_text()}"
        # 无时间全量分析时，附带"给时间可加速"的提示（供上层转达，不阻塞结果）
        if no_time_applied and not window_applied:
            text += (
                "\n\n（提示：若告知故障发生的大致时间，可用时间窗加速定位，速度更快。）"
            )
        cap = CapabilityResult(text=text, meta=meta, ok=True)

        # 清理临时窗口文件（分析完成后删除，避免残留）
        if tmp_path and tmp_path != log_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        return cap

    # ── 辅助 ──

    @staticmethod
    def _default_context(query: str) -> dict:
        """无 task_context 时的最小上下文（保证 LogSubAgent.analyze 能跑）。"""
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
        """检测日志属于哪个产品（多产品适配，呼应 §6c.1b）。"""
        try:
            from ai.agents.AiTaskPlatform.product_registry import pick_manual_dir
            d = pick_manual_dir(log_path)
            if d:
                return d
        except Exception:
            pass
        return "unknown"
