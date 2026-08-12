"""@AI 讨论流程 — 从 pipeline.py 拆分出的 Mixin

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
    # discuss — @AI 讨论回复
    # ============================================================

    async def discuss(self, task_id: str, query: str, context: dict) -> dict:
        """@AI 讨论：基于讨论历史 + 工单上下文 + 按需附件/历史工单 回复。"""
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

        # 3. 按需调附件分析 / 历史工单（日志和图片各自独立触发）
        from ai.agents.AiTaskPlatform.retrieval import rules as _rules
        from ai.agents.AiTaskPlatform.contexts import build_img_ctx, build_task_ctx

        facultative = ""
        log_keywords = _rules.DISCUSS_LOG_KEYWORDS
        img_keywords = _rules.DISCUSS_IMG_KEYWORDS
        hist_keywords = _rules.DISCUSS_HIST_KEYWORDS

        if query and ctx.attachments:
            q_lower = query.lower()

            # ── 3a. 图片分析（VLM + 文本两阶段）──
            if _rules.query_matches(q_lower, img_keywords):
                try:
                    from ai.agents.AiTaskPlatform.attachments.parser import analyze_images
                    img_ctx = build_img_ctx(ctx)
                    img_result = await analyze_images(ctx.attachments, img_ctx)
                    if img_result:
                        facultative += f"\n{img_result}\n"
                except Exception:
                    pass

            # ── 3b. 日志文件 → Orchestrator 循环编排（Discovery + 多轮指挥）──
            if _rules.query_matches(q_lower, log_keywords):
                try:
                    log_paths, _tmp_dirs = self._extract_log_paths(ctx.attachments)

                    if log_paths:
                        from ai.agents.AiTaskPlatform.orchestrator import LogOrchestrator
                        task_ctx = build_task_ctx(ctx)
                        orch = LogOrchestrator(self, log_paths[0])
                        orch_result = await orch.run(
                            task_ctx=task_ctx,
                            discussion_history=discussion_history,
                            query=query,
                        )
                        if orch_result.get("conclusion"):
                            faculty_parts = [f"【日志循环编排分析（{len(orch_result.get('rounds', []))}轮）】"]
                            faculty_parts.append(orch_result["conclusion"])
                            if orch_result.get("evidence"):
                                faculty_parts.append("关键日志行:")
                                for e in orch_result["evidence"][:8]:
                                    faculty_parts.append(f"  L{e['line']}: {e['summary'][:150]}")
                            facultative += "\n" + "\n".join(faculty_parts) + "\n"

                    for td in _tmp_dirs:
                        try:
                            import shutil
                            shutil.rmtree(td, ignore_errors=True)
                        except Exception:
                            pass
                except Exception:
                    pass

        # ── 3c. 代码检索（关键词触发）──
        code_keywords = _rules.DISCUSS_CODE_KEYWORDS
        if query and any(kw in query.lower() for kw in code_keywords):
            try:
                from ai.agents.AiTaskPlatform.code_skill.skill import get_code_skill
                skill = get_code_skill()
                skill.ensure_index()
                code_result = await skill.search(query)
                code_text = code_result.to_prompt_text()
                if code_text and "未找到" not in code_text:
                    facultative += f"\n[代码检索结果]\n{code_text}\n"
            except Exception as e:
                logger.warning(f"CodeSkill 检索失败: {e}")

        # ── 3d. 历史工单检索 ──
        if query and any(kw in query.lower() for kw in hist_keywords):
            try:
                query_text = self._build_query(ctx)
                hist = await self._retrieve_task_resolutions(query_text)
                if hist and "无" not in hist[:10]:
                    facultative += f"\n[历史相似工单]\n{hist[:500]}\n"
            except Exception:
                pass

        # 4. LLM
        if not facultative and query:
            att_mentions = _rules.ATTACHMENT_MENTION_WORDS
            if any(kw in query.lower() for kw in att_mentions):
                facultative = "⚠️ 当前工单没有日志、图片或任何可解析的附件。请如实告知工程师，不要编造。"

        diag_summary = f"推测: {' / '.join(ctx.hypotheses) if ctx.hypotheses else '无'}"
        prompt = DISCUSS_USER_TEMPLATE.format(
            title=ctx.title or "",
            description=(ctx.description or "")[:200],
            diagnosis_summary=diag_summary,
            discussion_history=discussion_history,
            query=query or "请基于讨论历史和工单信息，给出你的分析和建议。",
            facultative_analysis=facultative,
        )

        t_llm = time.perf_counter()
        reply = await self._llm_client.complete(
            prompt=prompt,
            system_prompt=_select_system_prompt(self._is_platform_ticket(ctx), "discuss"),
            max_tokens=600, temperature=0.4,
        )
        self._add_trace(self.NODE_LLM, "ok",
                        output={"reply_chars": len(reply)},
                        elapsed_ms=round((time.perf_counter() - t_llm) * 1000))

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
            "_trace": self._pop_trace(),
            "_total_ms": total_ms,
        }
