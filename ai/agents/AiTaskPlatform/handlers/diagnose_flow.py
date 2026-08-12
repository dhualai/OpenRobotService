"""诊断报告流程（[帮我分析] 按钮）— 从 pipeline.py 拆分出的 Mixin

含 AiTaskAgent 的 diagnose 方法（保持 self.xxx 调用不变，仅拆分文件）。
diagnose = 全量通盘：读评论 + Discovery/日志分析 + 历史/平台检索 → 一次性报告（不落库）。
"""

import json
import re
import time

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.prompts import DIAGNOSE_SYSTEM_PROMPT, DIAGNOSE_USER_TEMPLATE
from ai.agents.AiTaskPlatform.contexts import build_task_ctx, build_img_ctx

logger = get_logger("TASK_AGENT")


def _discovery_to_text(triage_result: dict) -> str:
    """把 triage(Discovery) 的结构化结果转成给诊断 LLM 看的文本。"""
    if not triage_result:
        return ""
    parts = ["【日志 Discovery 摘要】"]

    mod = triage_result.get("module") or {}
    if mod.get("name"):
        parts.append(f"日志所属模块: {mod['name']}")

    guide = triage_result.get("guide") or {}
    routed = guide.get("routed") or {}
    if routed.get("category", {}).get("name"):
        parts.append(f"手册路由: 产品类别={routed['category']['name']}, "
                     f"命中手册数={len(routed.get('selected') or [])}")

    scenario = triage_result.get("scenario") or {}
    if scenario.get("name"):
        parts.append(f"场景分类: {scenario['name']} (置信度 {scenario.get('confidence', 0)})")
        if scenario.get("template_hint"):
            parts.append(f"排查模板提示: {scenario['template_hint']}")

    # —— 错误级别统计优先（先看日志到底有没有 ERROR 输出，再谈稀有信号词）——
    lv = triage_result.get("level_dist") or {}
    if lv:
        if lv.get("has_error"):
            parts.append(
                f"错误级别分布: ERROR={lv.get('ERROR', 0)}, "
                f"WARNING={lv.get('WARNING', 0)}, FATAL={lv.get('FATAL', 0)}, "
                f"INFO={lv.get('INFO', 0)} (错误占比 {lv.get('err_ratio', 0)}%)"
            )
        else:
            parts.append(
                f"未发现 ERROR/WARNING 输出 (仅 {lv.get('INFO', 0)} 条 INFO, "
                f"共 {lv.get('total_tagged', 0)} 条带级别标注)"
            )

    top_errs = triage_result.get("top_errors") or []
    if top_errs:
        te = ", ".join(f"「{e['code']}」({e['count']})" for e in top_errs[:6])
        parts.append(f"Top 高频错误: {te}")

    signals = triage_result.get("signals") or []
    if signals:
        top = ", ".join(f"{s['name']}({s['count']})" for s in signals[:8])
        parts.append(f"Top 信号: {top}")

    hw = triage_result.get("hot_windows") or []
    if hw:
        w = hw[0]
        parts.append(f"异常最密集时段: {w.get('start')}~{w.get('end')} ({w.get('count')} 条)")

    ent = triage_result.get("entities") or {}
    robots = ent.get("robots") or []
    if robots:
        parts.append("该时段活跃车型: " + ", ".join(f"{r['id']}({r['count']})" for r in robots[:5]))

    facts = triage_result.get("facts") or {}
    if facts.get("time_start") and facts.get("time_end"):
        parts.append(f"日志时间范围: {facts['time_start']} ~ {facts['time_end']}")

    return "\n".join(parts)


class DiagnoseFlow:
    # ============================================================
    # diagnose — 诊断报告（[帮我分析] 按钮）
    # ============================================================

    async def diagnose(self, task_id: str) -> dict:
        """全能力诊断 → 即时返回报告 JSON（不存库）。

        使用能力：附件分析 + 历史工单检索。
        不检索排查树——提单 Agent 已经走过，结论在 diagnosis JSON 里。
        """
        t0 = time.perf_counter()
        self._pop_trace()
        await self._ensure_clients()

        # 1. 加载工单上下文
        t1 = time.perf_counter()
        context = await self._load_task_context(task_id)
        self._add_trace(self.NODE_LOAD_CONTEXT, "ok",
                        input={"task_id": task_id},
                        output={"has_title": bool(context.title),
                                "has_problem_summary": bool(context.problem_summary)},
                        elapsed_ms=round((time.perf_counter() - t1) * 1000))

        # 2. 附件分析（能力一：日志走 LogSubAgent+Discovery，其他走 parse_attachments）
        t2 = time.perf_counter()
        att_has_logs = False
        att_log_summary = ""
        log_sub_result = None
        try:
            if context.attachments:
                # 2a. 日志文件提取（压缩包自动解压到临时目录）
                log_paths, _tmp_dirs = self._extract_log_paths(context.attachments)

                if log_paths:
                    from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import LogSubAgent
                    from ai.agents.AiTaskPlatform.log_analyzer.triage import run_triage
                    task_ctx = build_task_ctx(context)
                    # 取第一个日志文件（后续可扩展到多个日志文件的合并分析）
                    sub = LogSubAgent(log_paths[0])
                    auto_question = f"日志分析，重点排查: {context.problem_summary}"
                    if context.hypotheses:
                        auto_question += f"，可能原因: {'/'.join(context.hypotheses)}"
                    if context.fault_code:
                        auto_question += f"，故障码: {context.fault_code}"
                    if context.robot_type:
                        auto_question += f"，车型: {context.robot_type}"

                    # Discovery 预处理（纯程序）→ 与 LogSubAgent 共享同一份 LogIndex（只 build 一次）
                    discovery_text = ""
                    try:
                        await sub._ensure_clients()  # 构建索引一次 + 初始化
                        triage_result = run_triage(log_paths[0], user_question=auto_question,
                                                   index=sub._index)
                        discovery_text = _discovery_to_text(triage_result)
                    except Exception as e:
                        logger.warning(f"诊断 Discovery 预处理失败: {e}")

                    log_sub_result = await sub.analyze(task_ctx, user_question=auto_question)
                    if log_sub_result.conclusion:
                        att_has_logs = True
                        parts = []
                        if discovery_text:
                            parts.append(discovery_text)
                        parts.append(log_sub_result.to_prompt_text())
                        att_log_summary = "\n\n".join(parts)
                    elif discovery_text:
                        att_has_logs = True
                        att_log_summary = discovery_text
                    self._add_trace(self.NODE_ATTACHMENT, "ok",
                                    output={"has_logs": att_has_logs,
                                            "sub_rounds": log_sub_result.queries_made,
                                            "evidence_count": len(log_sub_result.evidence)},
                                    elapsed_ms=round((time.perf_counter() - t2) * 1000))

                    import shutil
                    for td in _tmp_dirs:
                        try:
                            shutil.rmtree(td, ignore_errors=True)
                        except Exception:
                            pass

                # 2b. 非日志附件 → parser（图片/文档/结构化文件等，不含压缩包和日志）
                from ai.agents.AiTaskPlatform import rules as _rules
                _PIPED_EXTS = _rules.PIPED_LOG_EXTS
                non_log_atts = [a for a in context.attachments
                                if not (a.get("filename") or a.get("name") or "").lower().endswith(_PIPED_EXTS)]
                if non_log_atts and not att_has_logs:
                    from ai.agents.AiTaskPlatform.attachments.parser import parse_attachments
                    att_analysis = await parse_attachments(non_log_atts)
                    att_has_logs = att_has_logs or att_analysis.has_logs
                    if att_analysis.log_summary and not att_log_summary:
                        att_log_summary = att_analysis.log_summary[:500]

                # 2c. 图片附件 → 视觉 LLM 分析
                try:
                    from ai.agents.AiTaskPlatform.attachments.parser import analyze_images
                    img_ctx = build_img_ctx(context)
                    att_image_analysis = await analyze_images(context.attachments, img_ctx)
                    if att_image_analysis:
                        att_log_summary = (att_log_summary + "\n\n" + att_image_analysis).strip()
                except Exception:
                    pass
            else:
                self._add_trace(self.NODE_ATTACHMENT, "skipped", elapsed_ms=0)
        except Exception as e:
            self._add_trace(self.NODE_ATTACHMENT, "error",
                            input={"error": str(e)},
                            elapsed_ms=round((time.perf_counter() - t2) * 1000))

        # 3. 历史工单检索（能力二）
        t3 = time.perf_counter()
        hist_found = False
        hist_summary = ""
        try:
            query_text = self._build_query(context)
            history_text = await self._retrieve_task_resolutions(query_text)
            hist_found = history_text is not None and len(history_text) > 0 and "无" not in history_text[:20]
            hist_summary = history_text[:1000] if history_text else ""
            self._add_trace(self.NODE_KNOWLEDGE, "ok",
                            output={"found": hist_found},
                            elapsed_ms=round((time.perf_counter() - t3) * 1000))
        except Exception as e:
            self._add_trace(self.NODE_KNOWLEDGE, "error",
                            input={"error": str(e)},
                            elapsed_ms=round((time.perf_counter() - t3) * 1000))

        # 3b. 平台参考文档检索（仅服务号/平台问题启用）
        platform_ref = ""
        try:
            if self._is_platform_ticket(context):
                platform_text = await self._retrieve_platform_reference(self._build_query(context))
                if platform_text and "无" not in platform_text[:10]:
                    platform_ref = platform_text[:1000]
        except Exception:
            pass

        # 3c. 读取讨论评论（通盘诊断补充工程师与 AI 的一手线索与排查进展）
        user_discussion = ""
        try:
            user_discussion = self._load_discussion(task_id, limit=20)
        except Exception:
            user_discussion = ""

        # 4. LLM 综合分析
        t4 = time.perf_counter()
        att_text = att_log_summary if att_has_logs else "（无附件或无可解析内容）"
        hist_text = hist_summary if hist_found else "（无相似的历史工单方案）"

        fault_parts = []
        if context.fault_code:
            fault_parts.append(f"故障码: {context.fault_code}")
        if context.robot_type:
            fault_parts.append(f"车型: {context.robot_type}")
        if context.location:
            fault_parts.append(f"位置: {context.location}")
        fault_info = "\n".join(fault_parts) if fault_parts else "（无特殊故障信息）"

        prompt = DIAGNOSE_USER_TEMPLATE.format(
            title=context.title or "",
            description=context.description or "",
            task_type=context.task_type or "problem",
            priority=context.priority or "中",
            problem_summary=context.problem_summary or "（提单 Agent 未提供）",
            hypotheses="、".join(context.hypotheses) if context.hypotheses else "（无）",
            ruled_out="、".join(context.ruled_out) if context.ruled_out else "（无）",
            collected_info=json.dumps(context.collected_info, ensure_ascii=False) if context.collected_info else "（无）",
            rounds=context.diagnosis_rounds,
            fault_info=fault_info,
            platform_reference=platform_ref or "（非平台问题，跳过平台参考文档检索）",
            attachment_analysis=att_text,
            historical_solutions=hist_text,
            user_discussion=user_discussion or "（无用户讨论补充）",
        )

        raw = await self._llm_client.complete(
            prompt=prompt, system_prompt=DIAGNOSE_SYSTEM_PROMPT,
            max_tokens=1500, temperature=0.3,
        )
        self._add_trace(self.NODE_LLM, "ok",
                        input={"model": self._llm_client.model, "prompt_chars": len(prompt)},
                        output={"response_chars": len(raw)},
                        elapsed_ms=round((time.perf_counter() - t4) * 1000))

        # 5. 返回 Markdown 报告（无需 JSON 解析）
        t5 = time.perf_counter()
        report_md = raw.strip()
        conf = 0.0
        m = re.search(r'置信度[：:]\s*(\d+\.?\d*)', report_md)
        if m:
            try:
                conf = float(m.group(1))
            except ValueError:
                pass
        root_cause = report_md
        for line in report_md.split('\n'):
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and len(stripped) > 10:
                root_cause = stripped[:200]
                break
        self._add_trace(self.NODE_PARSE, "ok",
                        output={"report_chars": len(report_md), "confidence": conf},
                        elapsed_ms=round((time.perf_counter() - t5) * 1000))

        total_ms = round((time.perf_counter() - t0) * 1000)

        return {
            "task_id": task_id,
            "root_cause_analysis": root_cause,
            "suggested_actions": [],
            "references": [],
            "confidence": conf,
            "needs_more_info": conf < 0.5,
            "attachment_analysis": {"has_logs": att_has_logs, "summary": att_log_summary},
            "history_found": hist_found,
            "report_md": report_md,
            "_trace": self._pop_trace(),
            "_total_ms": total_ms,
        }
