"""诊断报告流程（[帮我分析] 按钮）— 从 pipeline.py 拆分出的 Mixin

含 AiTaskAgent 的 diagnose 方法（保持 self.xxx 调用不变，仅拆分文件）。
diagnose = 全量通盘：读评论 + Discovery/日志分析 + 历史/平台检索 → 一次性报告（不落库）。
"""

import asyncio
import json
import re
import time

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.prompts import (
    DIAGNOSE_SYSTEM_PROMPT, DIAGNOSE_USER_TEMPLATE,
    select_system_prompt as _select_system_prompt,
)
from ai.agents.AiTaskPlatform.contexts import build_task_ctx, build_img_ctx
# 复用 discuss 的进度广播封装（同一语义：事件封套 phase 只 running，收尾单独 done）
from ai.agents.AiTaskPlatform.handlers.discuss_flow import (
    _broadcast_ai_progress,
    _broadcast_ai_progress_await,
)

logger = get_logger("TASK_AGENT")


class _DiagProgress:
    """diagnose 过程区进度：按固定阶段累积 todo 并广播 ai.progress 到工单 WS 房间。

    与 discuss 同语义（见 memory：ai-progress-envelope-vs-item-phase）：
      - 事件封套 phase 只发 running，逐阶段更新单项 status/phase，前端实时看到"正在做哪一步"；
      - 收尾单独发一次 done（await 确保送达），前端据此收起过程区。
    """

    def __init__(self, task_id: str, run_id: str):
        self._task_id = task_id
        self._run_id = run_id
        self._todos: list[dict] = []
        self._idx: dict[str, dict] = {}

    def snapshot(self) -> list[dict]:
        return [dict(t) for t in self._todos]

    def seed(self, key: str, description: str, capability: str = "") -> None:
        """仅登记首个「进行中」待办项，不广播。

        首个 running 需由调用方用 await 版本确定性送达，前端才会建立执行过程区
        （与 discuss 同理：若首个 running 走 fire-and-forget 丢失，前端只收到收尾
        done 时过程区恒不显示）。
        """
        todo = {
            "id": key,
            "description": description,
            "status": "in_progress",
            "capability": capability,
            "phase": "running",
        }
        self._idx[key] = todo
        self._todos.append(todo)

    def _emit(self, phase: str = "running") -> None:
        try:
            _broadcast_ai_progress(self._task_id, self._run_id, self.snapshot(), phase)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[diagnose] ai.progress 广播失败 phase={phase} task={self._task_id}: {e}")

    def add(self, key: str, description: str, status: str = "in_progress", capability: str = "") -> None:
        """新增一个待办项（或更新已有项描述），并广播当前快照。"""
        todo = self._idx.get(key)
        if todo is None:
            todo = {
                "id": key,
                "description": description,
                "status": status,
                "capability": capability,
                "phase": "running" if status == "in_progress" else "done",
            }
            self._idx[key] = todo
            self._todos.append(todo)
        else:
            todo["description"] = description
        self._emit("running")

    def done(self, key: str, description: str | None = None, result_summary: str = "") -> None:
        """把指定待办项标记完成，并广播当前快照。"""
        todo = self._idx.get(key)
        if todo is None:
            # 兜底：没 add 过就直接补一条完成态（防御乱序）
            todo = {"id": key, "description": description or key,
                    "status": "completed", "capability": "", "phase": "done"}
            self._idx[key] = todo
            self._todos.append(todo)
        else:
            todo["status"] = "completed"
            todo["phase"] = "done"
            if description:
                todo["description"] = description
        if result_summary:
            todo["result_summary"] = result_summary[:80]
        self._emit("running")

    async def finish(self) -> None:
        """收尾：把残留 in_progress 归一化为 completed 并广播 done（await 确保送达）。"""
        for todo in self._todos:
            if todo.get("status") == "in_progress":
                todo["status"] = "completed"
            if todo.get("phase") in ("running", "in_progress"):
                todo["phase"] = "done"
        try:
            await _broadcast_ai_progress_await(self._task_id, self._run_id, self.snapshot(), "done")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[diagnose] ai.progress 收尾广播失败 task={self._task_id}: {e}")



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

    top_errs = triage_result.get("top_errors") or {}
    primary = top_errs.get("primary") or []
    warning = top_errs.get("warning") or []
    has_err = top_errs.get("has_error", False)
    if primary:
        if has_err:
            # 存在 ERROR：ERROR 根因优先呈现（明确标注），WARNING 作为次生信号
            te = ", ".join(f"「{e['code']}」({e['count']})" for e in primary[:6])
            parts.append(f"⚠️ ERROR 根因 (优先): {te}")
            if warning:
                tw = ", ".join(f"「{e['code']}」({e['count']})" for e in warning[:5])
                parts.append(f"WARNING 次生信号: {tw}")
        else:
            # 无 ERROR：退而用 WARNING 作为主信号
            te = ", ".join(f"「{e['code']}」({e['count']})" for e in primary[:6])
            parts.append(f"高频异常信号 (无ERROR，以WARNING为主): {te}")

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


# ── 关键信息查漏（P3）────────────────────────────────────────────
# 高价值前提：故障发生时间 / 是否可复现 / 变更了什么 / 现场信息。
# 核心原则（尊重提单已收集信息）：
#   提单 Agent 在 dialog 里通常已把这些信息收进 collected_info
#   （如 occurrence_time=发生时间 / frequency=每次|偶尔|首次，以及 description 会总结
#   "版本、发生时间"等关键信息）。**diagnose 不复查、不重复追问**——只有当某项
#   被确认缺失（collected_info 无对应键 且 描述/日志/讨论里也没有）时才建议补充。
# 且只列【缺失且对定位有决定性作用】的项，最多 3 条，杜绝"审问式"轰炸。
#
# 【产品无关原则（重要）】内核不绑死调度 USP：通用维度（时间/复现/变更）产品无关；
# 现场/动作维度 [按产品分流]——AGV/调度关注"车辆现场动作"，服务号平台关注"平台操作"，
# 通过 is_platform 参数选中对应关键词集，避免用调度词去套服务号问题。
_TIME_KW = ["时间", "几点", "上午", "下午", "昨天", "今天", "分", "时", "点", "发生",
            "occurred", "14:", "15:", "16:", "17:", "18:", "19:", "20:", "21:", "22:", "23:"]
_REPRO_KW = ["复现", "复现频率", "偶尔", "经常", "每次", "偶发", "一次", "多次", "连续", "间歇", "规律", "触发", "频率"]
_CHANGE_KW = ["变更", "升级", "版本", "更新", "部署", "配置", "改动", "新版本", "回退", "上线", "改", "调整"]
# 现场维度：AGV/调度（车辆动作）vs 服务号平台（平台操作）——按产品分流
_SCENE_KW_AGV = ["操作", "现场", "按了", "点了", "执行", "运行时", "步骤", "路径",
                 "搬运", "装载", "卸货", "充电", "避让", "对接", "入库", "取货"]
_SCENE_KW_PLATFORM = ["操作", "现场", "按了", "点了", "页面", "功能", "账号", "接口", "登录",
                      "权限", "按钮", "菜单", "报错", "页面加载", "配置了", "点击"]


def _has_any(text: str, kws) -> bool:
    t = (text or "").lower()
    return any(k in t for k in kws)


# collected_info 里可能承载"时间/频率"的键（提单 Agent 常用，做结构化识别）
_TIME_KEYS = {"occurrence_time", "时间", "发生时间", "occurred_at", "occurred", "time"}
_REPRO_KEYS = {"frequency", "复现", "频率", "复现频率", "reproducibility", "frequency_desc"}


def _ci_has(ci: dict, keys) -> bool:
    """collected_info 中是否有任一给定键且非空（结构化识别，避免 keyword 误判）。"""
    if not isinstance(ci, dict):
        return False
    for k in keys:
        v = ci.get(k)
        if v and str(v).strip() and str(v).strip().lower() not in ("无", "无无", "不清楚", "不知道", "暂无", "未知"):
            return True
    return False


def _info_gap_detect(context, att_has_logs: bool, att_log_summary: str,
                     user_discussion: str, is_platform: bool = False) -> list:
    """识别对定位有决定性作用、但当前**确实缺失**的关键信息。

    Args:
        is_platform: 是否为服务号平台问题（True=平台，关注平台操作；False=调度/AGV，关注车辆现场）。

    Returns:
        list[{key, question, why}]，最多 3 条；通常为空（提单已收集好）。
        每条 question 是可直接回答的表述（供前端/讨论引导）。
    """
    desc = (getattr(context, "description", None) or "") or ""
    ci = getattr(context, "collected_info", None) or {}
    if not isinstance(ci, dict):
        ci = {}
    ci_text = " ".join(str(v) for v in ci.values() if v)
    collated = f"{desc} {ci_text} {user_discussion or ''} {att_log_summary or ''}"

    # 现场关键词按产品分流
    scene_kw = _SCENE_KW_PLATFORM if is_platform else _SCENE_KW_AGV

    gaps = []

    # ① 故障发生时间：提单通常已收（occurrence_time）或描述里带着。
    #    仅当【有日志但全链路都没有时间】才建议——用于时间窗定向。
    if (
        att_has_logs
        and not _ci_has(ci, _TIME_KEYS)
        and not _has_any(collated, _TIME_KW)
    ):
        gaps.append({
            "key": "occurred_at",
            "question": "故障大概在什么时间发生？",
            "why": "有了时间可以只分析故障前那一段日志，定位更快更准",
        })

    # ② 是否可复现/频率：提单通常已收（frequency）。缺失且全文未见才建议。
    if (
        not _ci_has(ci, _REPRO_KEYS)
        and not _has_any(collated, _REPRO_KW)
    ):
        gaps.append({
            "key": "reproducible",
            "question": "这个问题是每次必现还是偶发？能不能稳定复现？",
            "why": "判断是版本缺陷还是偶发竞态，直接影响定位方向",
        })

    # ③ 变更了什么（产品无关，通用）：提单描述里若带"版本/配置"通常已含。缺失才建议。
    if not _has_any(collated, _CHANGE_KW):
        gaps.append({
            "key": "change",
            "question": ("故障发生前后有做过什么变更吗（升级/配置调整/新增功能/加车等）？"
                         if not is_platform else
                         "故障发生前后有做过什么变更吗（版本升级/配置调整/权限/账号等）？"),
            "why": "很多故障是变更引入的，知道变更能快速缩小范围",
        })

    # ④ 现场/操作信息（[按产品分流]）：
    #    服务号平台问题不问"车辆动作"（不适用），只问平台操作。
    if not _has_any(collated, scene_kw):
        if is_platform:
            gaps.append({
                "key": "scene",
                "question": "操作了什么功能/页面时出现的报错？具体操作步骤是什么？",
                "why": "平台问题的操作路径（页面/功能/接口）是定位的关键线索",
            })
        else:
            gaps.append({
                "key": "scene",
                "question": "故障发生时车在做什么（搬运/充电/避让等），操作了什么？",
                "why": "操作与故障的关联是常见定位线索",
            })

    return gaps[:3]


class DiagnoseFlow:
    # ============================================================
    # diagnose — 诊断报告（[帮我分析] 按钮）
    # ============================================================

    # 改造点 B / G2 并行化的三个独立只读小任务（各写回 self._diag_* 实例字段）
    async def _diag_load_history(self, context):
        """历史工单方案检索（独立只读）。"""
        try:
            query_text = self._build_query(context)
            history_text = await self._retrieve_task_resolutions(query_text)
            self._diag_hist_found = history_text is not None and len(history_text) > 0 and "无" not in history_text[:20]
            self._diag_hist_summary = history_text[:1000] if history_text else ""
        except Exception:
            self._diag_hist_found = False
            self._diag_hist_summary = ""

    async def _diag_load_platform(self, context):
        """平台参考文档检索（仅服务号/平台问题启用，独立只读）。"""
        self._diag_platform_ref = ""
        try:
            if self._is_platform_ticket(context):
                platform_text = await self._retrieve_platform_reference(self._build_query(context))
                if platform_text and "无" not in platform_text[:10]:
                    self._diag_platform_ref = platform_text[:1000]
        except Exception:
            self._diag_platform_ref = ""

    async def _diag_load_discussion(self, task_id):
        """读取讨论评论（独立只读）。"""
        try:
            self._diag_user_discussion = self._load_discussion(task_id, limit=20)
        except Exception:
            self._diag_user_discussion = ""

    async def diagnose(self, task_id: str) -> dict:
        """全能力诊断 → 即时返回报告 JSON（不存库）。

        使用能力：附件分析 + 历史工单检索。
        不检索排查树——提单 Agent 已经走过，结论在 diagnosis JSON 里。
        """
        t0 = time.perf_counter()
        self._pop_trace()
        await self._ensure_clients()

        # 过程区进度：与 discuss 同款 ai.progress（事件封套只 running，收尾 done），
        # 让 [帮我分析] 也能在讨论区上方实时看到 AI 正在执行哪一步（像 @AI 讨论一样）。
        run_id = f"{task_id}:{int(time.time() * 1000)}"
        prog = _DiagProgress(task_id, run_id)
        # 首个 running 必须 await 确定性送达 → 前端才建立「执行过程区」；
        # 否则只收到收尾 done 时过程区恒不显示（与 discuss 首条 running 同等处理）。
        prog.seed("planning", "正在加载工单上下文并规划分析", capability="planning")
        await _broadcast_ai_progress_await(task_id, run_id, prog.snapshot(), "running")

        # 1. 加载工单上下文
        t1 = time.perf_counter()
        context = await self._load_task_context(task_id)
        prog.done("planning", "已加载工单上下文")
        self._add_trace(self.NODE_LOAD_CONTEXT, "ok",
                        input={"task_id": task_id},
                        output={"has_title": bool(context.title),
                                "has_problem_summary": bool(context.problem_summary)},
                        elapsed_ms=round((time.perf_counter() - t1) * 1000))

        # 2. 附件分析（能力一：日志走 LogSubAgent+Discovery，其他走 parse_attachments）
        t2 = time.perf_counter()
        prog.add("attachment", "分析附件与日志", capability="log_analyze")
        att_has_logs = False
        att_log_summary = ""
        log_sub_result = None
        try:
            if context.attachments:
                # 2a. 日志文件提取（压缩包自动解压；带 task_id 落到稳定日志缓存目录跨讨论复用）
                log_paths, _tmp_dirs = self._extract_log_paths(
                    context.attachments,
                    task_id=getattr(context, "task_id", "") or "",
                )

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
                from ai.agents.AiTaskPlatform.retrieval import rules as _rules
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

        prog.done("attachment", "附件与日志分析完成",
                  result_summary=("含日志" if att_has_logs else "无附件") if not att_log_summary else att_log_summary)

        # 3. 历史工单检索 + 平台参考 + 讨论评论（改造点 B / G2 并行化：均为独立只读，用 gather 并行）
        t3 = time.perf_counter()
        prog.add("retrieval", "检索历史工单与平台资料", capability="retrieve_history")
        # 初始化并行任务写回字段（防御：若某任务异常也必须能读到默认值）
        self._diag_hist_found = False
        self._diag_hist_summary = ""
        self._diag_platform_ref = ""
        self._diag_user_discussion = ""
        await asyncio.gather(
            self._diag_load_history(context),
            self._diag_load_platform(context),
            self._diag_load_discussion(task_id),
        )
        hist_found = self._diag_hist_found
        hist_summary = self._diag_hist_summary
        platform_ref = self._diag_platform_ref
        user_discussion = self._diag_user_discussion
        self._add_trace(self.NODE_KNOWLEDGE, "ok",
                        output={"hist_found": hist_found, "platform": bool(platform_ref), "discussion": bool(user_discussion)},
                        elapsed_ms=round((time.perf_counter() - t3) * 1000))

        # 3.5 关键信息查漏（P3）：识别缺失但对定位有决定性作用的信息
        # 排查优先 + 一次性建议：报告主体照常生成，missing_info 只作为"补充建议"呈现，不阻塞、不打断。
        # 产品无关：is_platform 决定"现场"维度用平台操作词还是车辆动作词，避免用调度词套服务号。
        missing_info = _info_gap_detect(
            context, att_has_logs, att_log_summary, user_discussion,
            is_platform=self._is_platform_ticket(context),
        )
        self._add_trace(self.NODE_KNOWLEDGE, "ok",
                        output={"missing": [g["key"] for g in missing_info]},
                        elapsed_ms=0)
        prog.done("retrieval", "历史工单与平台资料检索完成",
                  result_summary=("命中历史方案" if hist_found else "无历史方案"))

        # 4. LLM 综合分析
        t4 = time.perf_counter()
        prog.add("llm", "综合分析并生成诊断报告", capability="llm")
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

        # 关键信息查漏（P3）：若存在缺失，作为"补充建议"注入 prompt，让报告末尾自然带出
        if missing_info:
            mi_lines = "\n".join(f"{i+1}. {g['question']}（{g['why']}）"
                                 for i, g in enumerate(missing_info))
            missing_info_text = (
                "以下是与定位相关的补充信息，**可能缺失**。请先基于现有材料给出分析；\n"
                "若确实需要这些信息才能更准定位，可在报告末尾用一两句【建议】补充，\n"
                "**不要为了追问而放弃分析**：\n"
                + mi_lines
            )
        else:
            missing_info_text = "（信息较充分，无需补充建议）"

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
            missing_info=missing_info_text,
        )

        raw = await self._llm_client.complete(
            prompt=prompt,
            system_prompt=_select_system_prompt(self._is_platform_ticket(context), "diagnose"),
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

        # 收尾：LLM 分析完成 + 整体 done（await 确保送达，前端据此收起执行过程区）
        prog.done("llm", "诊断报告已生成", result_summary=report_md[:80] or "完成")
        await prog.finish()

        # P3：needs_more_info 由"查漏出的缺失信息"驱动（而非仅 conf<0.5），
        # 更有意义——代表"确实有高价值信息缺失，建议补充"。
        return {
            "task_id": task_id,
            "root_cause_analysis": root_cause,
            "suggested_actions": [],
            "references": [],
            "confidence": conf,
            "needs_more_info": bool(missing_info) or conf < 0.5,
            "missing_info": missing_info,  # P3：缺失的高价值信息（供前端「🤔 还需确认」区块 / 讨论引导）
            "attachment_analysis": {"has_logs": att_has_logs, "summary": att_log_summary},
            "history_found": hist_found,
            "report_md": report_md,
            "_trace": self._pop_trace(),
            "_total_ms": total_ms,
        }
