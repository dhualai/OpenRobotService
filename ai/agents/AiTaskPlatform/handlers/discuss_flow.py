"""@U老师 讨论流程 — 从 pipeline.py 拆分出的 Mixin

含 AiTaskAgent 的 discuss 方法（保持 self.xxx 调用不变，仅拆分文件）。
discuss = 针对性：按 query 关键词触发日志/图片/代码/历史，组合讨论历史回复。
"""

import time

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.prompts import (
    DISCUSS_SYSTEM_PROMPT, DISCUSS_USER_TEMPLATE,
    select_system_prompt as _select_system_prompt,
)
# @# 跨工单引用解析/注入（模块顶层导入，避免运行时静默降级掩盖 import 错误）
from ai.agents.AiTaskPlatform.contexts import (
    extract_referenced_task_ids,
    format_referenced_tickets,
)

logger = get_logger("TASK_AGENT")


# ── 附件记忆「大脑决策」分类 ─────────────────────────────────────────
# 只维护 attachment_analysis（已解读附件记忆）。每次 discuss 据此决定读哪些附件：
#   - 未解读（object_path 不在记忆）         → 必须读文件分析
#   - 已解读 + 图片                          → 不重读，直接用记忆摘要（截图结论稳定）
#   - 已解读 + 日志/其他文件                 → 不默认重读，但交给大脑(Supervisor)按需重读
def _classify_attachments(ctx):
    """按记忆 + 扩展名把 ctx.attachments 分为 new（需读）与 known（已解读，含可重读附件）。

    Returns:
        (new_atts, known_map, known_atts, kind_of)
          new_atts:  需要本次真正读文件分析的附件 dict 列表
          known_map: {object_path: {filename, kind, summary}} 已解读摘要（供大脑参考）
          known_atts:已解读附件的完整 dict 列表（kind=non-image 的可在需要重读时取用）
          kind_of:   {object_path: 'image'|'log'|'doc'|'other'}
    """
    memo = getattr(ctx, "attachment_analysis", None) or {}
    new_atts = []
    known_map = {}
    known_atts = []
    kind_of = {}
    for att in ctx.attachments or []:
        if not isinstance(att, dict):
            continue
        obj = att.get("object_path") or att.get("path") or att.get("url") or ""
        fname = att.get("filename") or att.get("name") or ""
        ext = _attachment_kind(fname, obj)
        kind_of[obj] = ext
        mem = memo.get(obj) if obj and isinstance(memo, dict) else None
        if mem and mem.get("analyzed"):
            known_map[obj] = {
                "filename": fname,
                "kind": mem.get("kind") or ext,
                "summary": mem.get("summary", ""),
            }
            known_atts.append(att)
        else:
            new_atts.append(att)  # 未解读 → 本次必读
    return new_atts, known_map, known_atts, kind_of


def _attachment_kind(filename: str, path: str = "") -> str:
    """按扩展名粗略判断附件类型：image / log / doc / other。"""
    name = ((filename or path) or "").lower()
    for ext in (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"):
        if name.endswith(ext):
            return "image"
    for ext in (".log", ".txt", ".csv"):
        if name.endswith(ext):
            return "log"
    for ext in (".docx", ".pdf", ".xlsx", ".md", ".xls", ".doc"):
        if name.endswith(ext):
            return "doc"
    return "log" if ("log" in name) else "other"


def _build_progress_emitter(task_id, run_id, live_todo: dict):
    """构造 Supervisor 单项进度回调：累积 live_todo 并广播 ai.progress 到后端 WS。

    让前端在执行过程中实时看到"正在做哪一步 / 已完成哪步"。
    """
    def emitter(payload: dict) -> None:
        tid = payload.get("id")
        if tid is not None:
            live_todo[tid] = payload
        # 事件封套 phase 固定为 running：只要 Supervisor 还在派发能力（>0 项尚未收尾），
        # 前端就应保持「正在排查执行」的进行中状态（头部转圈 + 文案）。
        # 单项完成的 done 只体现在该 todo 项自身的 phase/status（图标 ✅），
        # 而**不是**整场执行完成——否则第一项一完成，头部就跳到"排查执行完成"，
        # 但剩下的项还在 ⏳，造成「完成了却还在转」的自相矛盾困惑。
        # 整场收尾的 done 由 _broadcast_ai_progress_await(..., "done") 单独发送。
        phase = "running"
        _broadcast_ai_progress(task_id, run_id, todos=list(live_todo.values()),
                              phase=phase)
    return emitter


def _broadcast_ai_progress(task_id, run_id, todos, phase: str) -> None:
    """跨进程通知后端把 AI 执行进度广播进该工单的 WS 房间（best-effort 不阻塞主流程）。"""
    try:
        from ai.agents.AiTaskPlatform.contexts import notify_backend_ai_progress
        notify_backend_ai_progress(task_id, run_id, todos, phase)
    except Exception as e:
        logger.warning(f"[discuss] ai.progress 广播失败 phase={phase} task={task_id}: {e}")


async def _broadcast_ai_progress_await(task_id, run_id, todos, phase: str) -> None:
    """整体阶段的确定性广播（await 等待发出后返回），用于收尾 done 信号。

    相比 best-effort 的火花线程，这里保证 done 一定送达后端，前端据此收起执行过程。
    """
    try:
        from ai.agents.AiTaskPlatform.contexts import notify_backend_ai_progress_await
        await notify_backend_ai_progress_await(task_id, run_id, todos, phase)
    except Exception as e:
        logger.warning(f"[discuss] ai.progress 收尾广播失败 phase={phase} task={task_id}: {e}")


class DiscussFlow:
    # ============================================================
    # discuss — @U老师 讨论回复
    # ============================================================

    async def discuss(self, task_id: str, query: str, context: dict) -> dict:
        """@U老师 讨论：基于讨论历史 + 工单上下文 + 按需附件/历史工单 回复。

        Supervisor 派发能力时的实时进度会通过后端 WS 广播 ai.progress，前端动态
        展示执行过程；最终回复只写纯粹答复（不含过程块）。
        """
        t0 = time.perf_counter()
        self._pop_trace()
        await self._ensure_clients()

        # 1. 工单上下文
        ctx = await self._load_task_context(task_id)

        # 1b. @# 跨工单引用（L2 注入）：解析用户 query 里 @#编号 引用的历史工单，
        #     预加载其上下文（基本信息 + diagnosis + solution + 讨论评论），
        #     作为"新加入的上下文"注入 prompt。预加载路径（Q3c=B）：不走 Supervisor。
        referenced_tickets = ""
        if query:
            try:
                _ref_ids = extract_referenced_task_ids(query)
                if _ref_ids:
                    referenced_tickets = format_referenced_tickets(_ref_ids)
                    self._add_trace(
                        self.NODE_DISCUSS, "ok",
                        output={"ticket_ref": _ref_ids},
                    )
            except Exception as _ref_e:
                logger.warning(f"[discuss] @# 引用工单注入失败: {_ref_e}")
                referenced_tickets = ""

        # 2. 讨论历史（能力三）
        recent = context.get("recent_comments", []) if context else []
        discussion_lines = []
        for c in recent[-10:]:
            author = c.get("author", c.get("created_by", "?"))
            content_str = str(c.get("content", ""))[:200]
            discussion_lines.append(f"[{author}] {content_str}")
        discussion_history = "\n".join(discussion_lines) if discussion_lines else "（暂无讨论）"

        # 3. Supervisor 自主调度能力（方案甲全量收敛：图片/日志/代码/历史都由调度 LLM 决定）
        #    替代原先 3a/3b/3c/3d 写死的关键词触发。
        from ai.agents.AiTaskPlatform.capabilities import Supervisor, CapabilityRegistry
        from ai.agents.AiTaskPlatform.contexts import build_img_ctx

        facultative = ""
        reasoning_trace = {}  # 透明化 planning（G6）：记录 Supervisor 的调度 plan/todo
        available_caps = CapabilityRegistry.list_available()  # 含 log_analyze（本版全量收敛）

        # 3.0 附件记忆「大脑决策」：区分本次需新读的附件与历史已解读摘要
        new_atts, known_map, known_atts, kind_of = _classify_attachments(ctx)

        # 运行时上下文（供能力取资源；attachments 默认只给"本次需新读"的附件）
        runtime_ctx = {
            "attachments": new_atts,          # 默认只读新附件（能力据此分析）
            "all_attachments": ctx.attachments or [],   # 全量（需要时扩展）
            "attachment_memory": known_map,   # 已解读附件的摘要（能力/LLM 参考，不必重读）
            "retriever": self._retriever,
            # 当前工单上下文（供 ticket_ref 在"无 @#编号、需大脑按需检索相似工单"时作检索基准）
            "current_task": {
                "task_id": getattr(ctx, "task_id", "") or task_id,
                "title": ctx.title or "",
                "description": ctx.description or "",
                "problem_summary": ctx.problem_summary or "",
                "fault_code": ctx.fault_code or "",
                "robot_type": ctx.robot_type or "",
            },
        }
        # @# 确定性引用已在本函数入口预加载注入（Q3c=B 主路径）→ 让大脑不再派发 ticket_ref，
        # 避免对同一个 @#编号 重复注入。只有当入口 query 没有顶层 @#（没有预加载）时，
        # 才保留 ticket_ref 给大脑"按需检索相似工单"（形态 C 大脑决策版）。
        if referenced_tickets:
            available_caps = [c for c in available_caps if c != "ticket_ref"]
        if ctx.attachments:
            runtime_ctx["img_ctx"] = build_img_ctx(ctx)
            try:
                log_paths, _tmp_dirs = self._extract_log_paths(new_atts)
                if log_paths:
                    runtime_ctx["log_path"] = log_paths[0]
            except Exception:
                log_paths, _tmp_dirs = [], []
            runtime_ctx.setdefault("_tmp_dirs", _tmp_dirs)

        # 3.0b 大脑重读决策（确定性走廊 + 记忆）：
        #   - 新增附件必读（记忆判断，already in new_atts）
        #   - 历史图片：不重读（摘要已够）
        #   - 历史日志/文件：若用户 query 涉及日志/分析 → 本次重读（含新+旧，拼完整时间线）
        from ai.agents.AiTaskPlatform.retrieval import rules as _rules
        analyzed_atts = list(runtime_ctx["attachments"])  # 已计划要读的（当前=new_atts）
        if query and _rules.query_matches(query, _rules.DISCUSS_LOG_KEYWORDS):
            # 把已解读的非图片附件（日志/文档）也纳入重读，支持结合最新历史
            for att in known_atts:
                obj2 = att.get("object_path") or att.get("path") or att.get("url") or ""
                if kind_of.get(obj2) in ("log", "doc", "other"):
                    analyzed_atts.append(att)
            if analyzed_atts:
                runtime_ctx["attachments"] = analyzed_atts
                try:
                    lpaths, _td = self._extract_log_paths(analyzed_atts)
                    if lpaths:
                        runtime_ctx["log_path"] = lpaths[0]
                        if _td:
                            runtime_ctx.setdefault("_tmp_dirs", []).extend(_td)
                except Exception:
                    pass

        # 3.1 构造给调度 LLM 看的能力描述清单（只把"当前可用的、有意义的"交给它）
        #   - 确定性程序护栏：能力所需资源缺失时直接从可用清单剔除，避免 LLM 派发必然失败的能力
        #     （如工单没有日志 → 不提供 log_analyze；没有非图片附件 → 不提供 attachment_parse）。
        _cap_att = runtime_ctx.get("attachments") or []
        _cap_all = ctx.attachments or []
        _kinds = set(kind_of.values())
        _has_log = bool(runtime_ctx.get("log_path"))
        _has_image = "image" in _kinds
        _has_non_image = bool(_kinds & {"log", "doc", "other"})
        # 附件的 object_path 集合（供 image_analyze 判断是否有可分析的图片）
        _img_paths = [a for a in _cap_all if kind_of.get(a.get("object_path") or a.get("path") or "") == "image"]

        _RESOURCE_GUARD = {
            "log_analyze": _has_log,                       # 需日志路径
            "attachment_parse": _has_non_image,            # 需非图片附件（日志/文档）
            "image_analyze": bool(_has_image or _img_paths),  # 需图片附件
        }
        available_caps = [
            c for c in available_caps
            if _RESOURCE_GUARD.get(c, True)  # 未在守卫表内的能力（历史/代码/排查树等）视为可用
        ]
        if available_caps != CapabilityRegistry.list_available():
            removed = [c for c in CapabilityRegistry.list_available() if c not in available_caps]
            logger.info(f"[discuss] 按资源剔除不可用能力: {removed}（可用: {available_caps}）")

        cap_hint = ", ".join(available_caps) or "（无可用能力）"
        has_att = bool(ctx.attachments)
        has_logs = bool(runtime_ctx.get("log_path"))

        # 已解读摘要的简述（让大脑知道历史结论，不必重读；日志可再读）
        known_lines = []
        for obj, rec in known_map.items():
            kind_label = {"image": "图片", "log": "日志", "doc": "文档"}.get(rec.get("kind"), rec.get("kind"))
            known_lines.append(f"- [{kind_label}] {rec.get('filename') or obj}: {rec.get('summary') or '（已分析）'}")
        known_txt = "\n".join(known_lines) if known_lines else "（无）"

        new_lines = []
        for att in new_atts:
            obj = att.get("object_path") or att.get("path") or ""
            fname = att.get("filename") or att.get("name") or ""
            new_lines.append(f"- {kind_of.get(obj, 'other')}: {fname or obj}")
        new_txt = "\n".join(new_lines) if new_lines else "（无）"

        task_ctx_for_plan = (
            f"工单: {ctx.title or ''}\n"
            f"描述: {(ctx.description or '')[:200]}\n"
            f"假设: {' / '.join(ctx.hypotheses) if ctx.hypotheses else '无'}\n"
            f"用户问题: {query or ''}\n"
            f"本次新增/未解读附件（需重点分析）:\n{new_txt}\n"
            f"历史已解读附件摘要（结论已存在，图片无需重读；日志如需结合最新可重读）:\n{known_txt}\n"
            f"可用能力: {cap_hint}\n\n"
            "根据用户问题和附件情况，决定派哪些能力分析（图片/日志/历史/代码）。\n"
            "规则：新增附件必分析；历史图片一般用其摘要即可（除非用户明确要重看）；\n"
            "历史日志若用户要'最新/完整/综合分析'，可对该日志能力在 params 中带上 reanalyze_logs=true 以重读。\n"
            "若问题仅需知识问答、无需分析附件，则 complexity=simple 不派生任何能力。"
        )

        # 3.2 是否值得走 Supervisor 调度？（有附件或明显需要工具时才调度，避免纯闲聊也调 LLM 调度器）
        from ai.agents.AiTaskPlatform.retrieval import rules as _rules
        need_supervisor = bool(
            query
            and (has_att or any(kw in query.lower() for kw in _rules.DISCUSS_HIST_KEYWORDS))
        )

        # 3.2b LLM 意图路由（改造点 A / G1）：识别纯闲聊 → 走短 prompt 快路径，
        #      不派生任何工具/子 Agent，省一次 Supervisor 调度 + token。
        is_pure_chat = False
        if query:
            try:
                from ai.agents.AiTaskPlatform.capabilities import Router
                intent = await Router.classify(
                    llm_client=self._llm_client,
                    query=query,
                    has_attachments=has_att,
                    has_logs=has_logs,
                    fallback="general",
                )
                is_pure_chat = (intent == "pure_chat")
                self._add_trace(self.NODE_DISCUSS, "ok", output={"router_intent": intent})
            except Exception:
                is_pure_chat = False

        if need_supervisor and available_caps and not is_pure_chat:
            supervisor = Supervisor(llm_client=self._llm_client)

            # 实时进度流（改造点 G6.5 / 动态执行过程）：
            #  - 每项能力 running/done 时，通过后端 WS 广播 ai.progress，前端边跑边展示；
            #  - 同时把最新状态累积到 reasoning_trace.todo（供最终返回 + 失败保底）。
            run_id = f"{task_id}:{int(time.time() * 1000)}"
            _live_todo: dict[str, dict] = {}
            _emit_progress = _build_progress_emitter(
                task_id=task_id, run_id=run_id, live_todo=_live_todo,
            )

            # 起始 running 信号（确定性广播）：前端只有在收到 phase=running 时才会建立
            # aiRunId 并显示「执行过程区」（DiscussionPanel.showAiProcess）。若首条 running
            # 走 fire-and-forget 而丢失，前端只收到收尾 done 时过程区恒不显示。故这里用
            # await 版本先确保送达一条带占位项的 running；后续逐项 running/done 仍走
            # best-effort（容忍丢失），收尾 done 保持 await 确定性送达。
            await _broadcast_ai_progress_await(
                task_id, run_id,
                todos=[{
                    "id": "planning",
                    "description": "正在分析任务并规划排查步骤",
                    "status": "in_progress",
                    "capability": "",
                    "phase": "running",
                }],
                phase="running",
            )

            # 把进度回调注入运行时上下文，供子 Agent（如 LogSubAgent）内部上报子步骤
            runtime_ctx["progress_emitter"] = _emit_progress

            sup_result = await supervisor.run(
                task_context=task_ctx_for_plan,
                available_caps=available_caps,
                runtime_ctx=runtime_ctx,
                on_progress=_emit_progress,
            )
            # 把各能力结果拼进 facultative
            if sup_result.get("results"):
                for cap_name, res in sup_result["results"].items():
                    if isinstance(res, dict) and res.get("text"):
                        label = {
                            "image_analyze": "图片分析",
                            "log_analyze": "日志分析",
                            "code_search": "代码检索",
                            "retrieve_history": "历史相似工单",
                        }.get(cap_name, cap_name)
                        facultative += f"\n[{label}]\n{res['text']}\n"
                    elif isinstance(res, dict) and not res.get("ok"):
                        # 能力失败：记录告警（无发生时间的日志提示已内嵌在结果文本里，不再单独阻断）
                        logger.warning(f"[discuss] 能力 {cap_name} 失败: {res.get('error')}")

            # ── 确定性保底（仅当用户明确要求"分析全部/所有附件/图片"时）——强制补做图片分析 ──
            # LLM 调度偶发只派 attachment_parse（解析文本附件）而不派 image_analyze，
            # 导致"分析全部附件"时截图不被识别。但**不做成无条件的**：用户没明确要求全面分析
            # 图片时，是否派 image_analyze 尊重 Supervisor/用户自己的意图（如用户单独说"分析一下图片"）。
            _ask_all_media = any(kw in query for kw in ("全部", "所有", "全部附件", "所有附件", "分析全部"))
            if (
                _ask_all_media
                and "image_analyze" in available_caps
                and _img_paths
                and "image_analyze" not in sup_result.get("results", {})
            ):
                try:
                    from ai.agents.AiTaskPlatform.capabilities import CapabilityRegistry
                    _img_cap = CapabilityRegistry.get("image_analyze")
                    if _img_cap is not None and _img_cap.is_available():
                        # 复用已回退全量的运行时附件（含图片 object_path），走 image_analyze
                        _img_kw = {
                            "query": "分析工单中的全部图片/截图，识别其中的车辆状态、路径与异常信息",
                            "img_ctx": runtime_ctx.get("img_ctx"),
                            "attachments": _img_paths,          # 仅图片附件
                            "all_attachments": _cap_all,        # 全量兜底
                        }
                        _img_res = await _img_cap(**_img_kw)  # 统一入口 __call__（含配额/异常兜底）
                        _res_dict = _img_res.to_dict() if hasattr(_img_res, "to_dict") else _img_res
                        if isinstance(_res_dict, dict) and _res_dict.get("text"):
                            facultative += f"\n[图片分析]\n{_res_dict['text']}\n"
                            sup_result.setdefault("results", {})["image_analyze"] = _res_dict
                            # 同步补一条 todo（进过程区展示）
                            _live_todo_txt = _res_dict["text"]
                            _live_todo.setdefault("image_analyze", {
                                "id": f"img_{len(_live_todo) + 1}",
                                "description": "分析工单中的图片/截图，识别车辆状态与路径信息",
                                "status": "completed",
                                "capability": "image_analyze",
                                "phase": "done",
                                "result_summary": _live_todo_txt[:80],
                            })
                            logger.info("[discuss] 已强制补做 image_analyze（图片保底分析）")
                except Exception as e:
                    logger.warning(f"[discuss] 强制 image_analyze 失败: {e}")

            self._add_trace(self.NODE_ATTACHMENT, "ok",
                            output={"supervisor_caps": list(sup_result.get("results", {}).keys())})

            # 透明化 planning（G6）：把 Supervisor 的调度决策、plan、todo 暴露给前端
            final_todo = sup_result.get("todo", [])
            if _live_todo:
                # 用实时进度流累积的 todo 覆盖（含每项状态，前端能拿到完整过程）
                merged = []
                for item in final_todo:
                    live = _live_todo.get(item.get("id"))
                    merged.append(live if live else item)
                final_todo = merged
            # 补充强制 image_analyze 的 todo（若存在），保证过程区能看到图片分析半步
            _img_todo = _live_todo.get("image_analyze")
            if _img_todo and not any(t.get("capability") == "image_analyze" for t in final_todo):
                final_todo = final_todo + [_img_todo]
            reasoning_trace = {
                "complexity": sup_result.get("complexity"),
                "plan": sup_result.get("plan", []),
                "todo": final_todo,
                "decision": sup_result.get("_decision"),
                "run_id": run_id,
            }

            # 收尾：整体完成广播（前端据此收起执行过程、仅展示纯回复）。
            # 用 await 版本确保 done 一定送达后端，避免出现"一直转不停"。
            # 兜底：把任何残留 in_progress 项归一化为 completed，保证「完成」封套内的
            # 每一项都是完成态，绝不出现「头部说完成、单项还在转圈」的矛盾。
            _final_todo = []
            for _t in final_todo:
                _t = dict(_t)
                if _t.get("status") == "in_progress":
                    _t["status"] = "completed"
                if _t.get("phase") in ("running", "in_progress"):
                    _t["phase"] = "done"
                _final_todo.append(_t)
            final_todo = _final_todo
            await _broadcast_ai_progress_await(task_id, run_id, final_todo, "done")

            # 清理临时目录（如日志解压）
            for td in runtime_ctx.get("_tmp_dirs", []):
                try:
                    import shutil
                    shutil.rmtree(td, ignore_errors=True)
                except Exception:
                    pass
        else:
            # 无附件且非工具类问题 → 不走 Supervisor，直接知识问答（facultative 为空）
            pass

        # 3.9 写回附件记忆：本次实际读取分析的附件标记为已解读，供下次「大脑」判断不再重复
        if facultative or runtime_ctx.get("attachments"):
            _read_atts = runtime_ctx.get("attachments") or []
            if _read_atts:
                _updates = {}
                _summary = (facultative or "").strip()[:800]
                for _att in _read_atts:
                    if not isinstance(_att, dict):
                        continue
                    _obj = _att.get("object_path") or _att.get("path") or _att.get("url") or ""
                    if not _obj:
                        continue
                    _updates[_obj] = {
                        "kind": kind_of.get(_obj, "other"),
                        "summary": _summary or _att.get("filename") or _obj,
                    }
                if _updates:
                    try:
                        from ai.core.task_adapter import update_attachment_analysis
                        update_attachment_analysis(task_id, _updates)
                    except Exception as _e:
                        logger.warning(f"[discuss] 写回附件记忆失败: {_e}")

        # 4. LLM（纯闲聊走 light 短 prompt，省 token）
        if is_pure_chat:
            from ai.agents.AiTaskPlatform.prompts import (
                DISCUSS_LIGHT_SYSTEM_PROMPT, DISCUSS_LIGHT_USER_TEMPLATE,
            )
            prompt = DISCUSS_LIGHT_USER_TEMPLATE.format(
                title=ctx.title or "",
                description=(ctx.description or "")[:200],
                discussion_history=discussion_history,
                query=query or "",
            )
            system_prompt = DISCUSS_LIGHT_SYSTEM_PROMPT
            max_tokens = 200
        else:
            if not facultative and query:
                att_mentions = _rules.ATTACHMENT_MENTION_WORDS
                if any(kw in query.lower() for kw in att_mentions):
                    facultative = "当前工单没有日志、图片或任何可解析的附件。请如实告知工程师，不要编造。"

            diag_summary = f"推测: {' / '.join(ctx.hypotheses) if ctx.hypotheses else '无'}"
            prompt = DISCUSS_USER_TEMPLATE.format(
                title=ctx.title or "",
                description=(ctx.description or "")[:200],
                diagnosis_summary=diag_summary,
                discussion_history=discussion_history,
                query=query or "请基于讨论历史和工单信息，给出你的分析和建议。",
                referenced_tickets=referenced_tickets or "",
                facultative_analysis=facultative,
            )
            system_prompt = _select_system_prompt(self._is_platform_ticket(ctx), "discuss")
            max_tokens = 600

        t_llm = time.perf_counter()
        reply = await self._llm_client.complete(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens, temperature=0.4,
        )
        self._add_trace(self.NODE_LLM, "ok",
                        output={"reply_chars": len(reply)},
                        elapsed_ms=round((time.perf_counter() - t_llm) * 1000))

        # 4.5 Evaluator-optimizer（改造点 C/G4）：仅对"需工具的讨论"启用（成本护栏，纯闲聊不启用）
        eval_used = False
        if facultative and reply.strip():
            try:
                from ai.agents.AiTaskPlatform.capabilities import Evaluator
                eval_res = await Evaluator.evaluate_and_rewrite(
                    llm_client=self._llm_client,
                    draft=reply,
                    evidence=facultative[:1500],       # 引用的证据（日志/图片/历史片段）
                    context=f"工单: {ctx.title or ''}\n用户问题: {query or ''}",
                )
                if eval_res.get("rewritten") or eval_res.get("eval_failed"):
                    eval_used = True
                if eval_res.get("rewritten"):
                    reply = eval_res["final"]
                    self._add_trace(self.NODE_LLM, "ok",
                                    output={"evaluator": "rewritten", "issues": eval_res.get("eval_notes", []), "reply_chars": len(reply)})
                elif eval_res.get("eval_failed"):
                    self._add_trace(self.NODE_LLM, "ok",
                                    output={"evaluator": "eval_failed", "reply_chars": len(reply)})
            except Exception as e:
                logger.warning(f"[discuss] Evaluator 执行异常，沿用初稿: {e}")

        # 5. 回复写入 task_comments
        #    最终评论只写入纯粹答复（不含"分析过程"）——执行过程已通过 ai.progress
        #    WS 事件在前端动态展示，不污染最终回复。
        comment_reply = reply.strip()
        try:
            self._add_diagnosis_comment_short(int(task_id), comment_reply)
        except Exception:
            pass

        total_ms = round((time.perf_counter() - t0) * 1000)
        return {
            "task_id": task_id,
            "reply": reply.strip(),
            "comment_id": None,
            "reasoning_trace": reasoning_trace,  # 透明化 planning（G6）
            "_trace": self._pop_trace(),
            "_total_ms": total_ms,
        }
