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

logger = get_logger("TASK_AGENT")


class DiscussFlow:
    # ============================================================
    # discuss — @U老师 讨论回复
    # ============================================================

    async def discuss(self, task_id: str, query: str, context: dict) -> dict:
        """@U老师 讨论：基于讨论历史 + 工单上下文 + 按需附件/历史工单 回复。"""
        t0 = time.perf_counter()
        self._pop_trace()
        await self._ensure_clients()

        # 1. 工单上下文
        ctx = await self._load_task_context(task_id)

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

        # 3.0 构建运行时上下文（供各能力取资源，调度 LLM 不需要知道这些）
        runtime_ctx = {"attachments": ctx.attachments, "retriever": self._retriever}
        if ctx.attachments:
            runtime_ctx["img_ctx"] = build_img_ctx(ctx)
            try:
                log_paths, _tmp_dirs = self._extract_log_paths(ctx.attachments)
                if log_paths:
                    runtime_ctx["log_path"] = log_paths[0]
            except Exception:
                log_paths, _tmp_dirs = [], []
            runtime_ctx.setdefault("_tmp_dirs", _tmp_dirs)

        # 3.1 构造给调度 LLM 看的能力描述清单（只把"当前可用的、有意义的"交给它）
        cap_hint = ", ".join(available_caps) or "（无可用能力）"
        has_att = bool(ctx.attachments)
        has_logs = bool(runtime_ctx.get("log_path"))
        task_ctx_for_plan = (
            f"工单: {ctx.title or ''}\n"
            f"描述: {(ctx.description or '')[:200]}\n"
            f"假设: {' / '.join(ctx.hypotheses) if ctx.hypotheses else '无'}\n"
            f"用户问题: {query or ''}\n"
            f"附件: {'有' if has_att else '无'}" + (f"（含日志: 是）" if has_logs else f"（含日志: 否）") + "\n"
            f"可用能力: {cap_hint}\n\n"
            "根据用户问题和工单信息，决定要派哪些能力来分析（图片/日志/历史/代码）。"
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
            sup_result = await supervisor.run(
                task_context=task_ctx_for_plan,
                available_caps=available_caps,
                runtime_ctx=runtime_ctx,
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
            self._add_trace(self.NODE_ATTACHMENT, "ok",
                            output={"supervisor_caps": list(sup_result.get("results", {}).keys())})

            # 透明化 planning（G6）：把 Supervisor 的调度决策、plan、todo 暴露给前端
            reasoning_trace = {
                "complexity": sup_result.get("complexity"),
                "plan": sup_result.get("plan", []),
                "todo": sup_result.get("todo", []),
                "decision": sup_result.get("_decision"),
            }

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
        try:
            self._add_diagnosis_comment_short(int(task_id), reply.strip())
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
