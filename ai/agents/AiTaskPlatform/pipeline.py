"""任务 Agent 核心流水线：上下文加载 → 三路分析 → LLM 生成 → 方案输出

职责边界（硬编码）：
    - 不做诊断（提单 Agent 已完成）
    - 不复诊 hypotheses / ruled_out / collected_info
    - 只检索排查树结论节点 + 历史工单方案 + 附件解析
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import httpx
from typing import Optional, AsyncGenerator
from dataclasses import dataclass, field

from ai.config import get_ai_config
from ai.core import get_llm_client, get_memory_manager, get_retrieval_service
from ai.agents.AiTaskPlatform.schemas import (
    TaskAnalyzeRequest,
    TaskContext,
    SolutionDraft,
    AttachmentAnalysis,
)
from ai.agents.AiTaskPlatform.prompts import (
    DIAGNOSE_SYSTEM_PROMPT,
    DIAGNOSE_USER_TEMPLATE,
    DISCUSS_SYSTEM_PROMPT,
    DISCUSS_USER_TEMPLATE,
    SUMMARIZE_SYSTEM_PROMPT,
    SUMMARIZE_FULL_TEMPLATE,
    SUMMARIZE_INCREMENTAL_TEMPLATE,
)


# ============================================================
# 核心类
# ============================================================

class AiTaskAgent:
    """任务 Agent：分析工单 → 生成解决方案草稿"""

    # ── 可追踪的流程节点（供测试 Agent 对照）──
    NODE_OVERHEAD = "overhead"          # 端点路由 + 客户端初始化
    NODE_LOAD_CONTEXT = "load_context"  # 加载工单上下文（task_adapter 读 tasks）
    NODE_RETRIEVE = "retrieve"          # 三路并行分析
    NODE_ATTACHMENT = "attachment"      # 附件分析（日志子Agent/parser）
    NODE_KNOWLEDGE = "knowledge"        # 历史工单检索
    NODE_BUILD_PROMPT = "build_prompt"  # Prompt 构建
    NODE_LLM = "llm"                    # LLM 调用（DeepSeek API）
    NODE_PARSE = "parse"                # 结果解析（JSON→SolutionDraft）
    NODE_DIAGNOSE = "diagnose"          # 诊断报告生成
    NODE_DISCUSS = "discuss"            # @AI 讨论回复
    NODE_SUMMARIZE = "summarize"        # 讨论摘要
    NODE_MEMORY = "memory"              # 记忆保存（Redis）
    NODE_COMMENT = "comment"            # 诊断结果写入 task_comments
    NODE_SUBMIT = "submit"              # 方案提交（tasks 表 + Qdrant 回写）

    def __init__(self):
        self.config = get_ai_config()
        self._llm_client = None
        self._retriever = None
        self._memory = None
        self._trace: list[dict] = []

    async def _ensure_clients(self):
        """懒加载 AI 核心服务单例"""
        t0 = time.perf_counter()
        if self._llm_client is None:
            self._llm_client = await get_llm_client()
        if self._retriever is None:
            self._retriever = await get_retrieval_service()
        if self._memory is None:
            self._memory = await get_memory_manager()
        self._add_trace(self.NODE_OVERHEAD, "ok", elapsed_ms=round((time.perf_counter() - t0) * 1000))

    # ── 埋点 ──────────────────────────────────────────────────

    def _add_trace(self, node: str, status: str, **kwargs):
        """追加一条追踪记录。status: ok | error | skipped"""
        entry = {"node": node, "status": status, "ts": round(time.perf_counter() * 1000)}
        entry.update(kwargs)
        self._trace.append(entry)

    def _pop_trace(self) -> list[dict]:
        """取出全部追踪记录并清空（每次请求独立）"""
        trace = self._trace
        self._trace = []
        return trace

    # ============================================================
    # analyze（非流式）
    # ============================================================

    async def analyze(self, request: TaskAnalyzeRequest) -> SolutionDraft:
        """非流式分析工单 → 返回结构化方案草稿"""
        t0 = time.perf_counter()
        self._pop_trace()  # 清理上次请求的残留
        await self._ensure_clients()

        # 1. 加载工单上下文
        t1 = time.perf_counter()
        context = await self._load_task_context(request.task_id)
        self._add_trace(self.NODE_LOAD_CONTEXT, "ok",
                        input={"task_id": request.task_id},
                        output={
                            "has_title": bool(context.title),
                            "has_problem_summary": bool(context.problem_summary),
                            "hypotheses_count": len(context.hypotheses),
                            "ruled_out_count": len(context.ruled_out),
                        },
                        elapsed_ms=round((time.perf_counter() - t1) * 1000))

        # 2. 三路并行分析
        t2 = time.perf_counter()
        retrieval_results = await self._run_analysis(context)
        self._add_trace(self.NODE_RETRIEVE, "ok",
                        input={"query": self._build_query(context)},
                        output={
                            "troubleshooting_len": len(retrieval_results.get("troubleshooting", "")),
                            "history_len": len(retrieval_results.get("history", "")),
                            "has_attachment_analysis": bool(retrieval_results.get("attachment_analysis")),
                        },
                        elapsed_ms=round((time.perf_counter() - t2) * 1000))

        # 3. 构建 Prompt
        t3 = time.perf_counter()
        prompt = self._build_prompt(context, retrieval_results)
        self._add_trace(self.NODE_BUILD_PROMPT, "ok",
                        input={"prompt_chars": len(prompt)},
                        elapsed_ms=round((time.perf_counter() - t3) * 1000))

        # 4. LLM 生成
        t4 = time.perf_counter()
        raw = await self._llm_client.complete(
            prompt=prompt,
            system_prompt=TASK_AGENT_SYSTEM_PROMPT,
            max_tokens=1500,
            temperature=0.3,
        )
        self._add_trace(self.NODE_LLM, "ok",
                        input={"model": self._llm_client.model, "max_tokens": 1500},
                        output={"response_chars": len(raw)},
                        elapsed_ms=round((time.perf_counter() - t4) * 1000))

        # 5. 解析
        t5 = time.perf_counter()
        draft, parse_status = self._parse_solution_with_status(raw)
        self._add_trace(self.NODE_PARSE, parse_status,
                        output={"confidence": draft.confidence, "actions_count": len(draft.suggested_actions)},
                        elapsed_ms=round((time.perf_counter() - t5) * 1000))

        # 6. 诊断结果写入 task_comments（AI任务助手评论）
        t6 = time.perf_counter()
        try:
            task_id_int = int(request.task_id)
            self._add_diagnosis_comment(task_id_int, draft)
            self._add_trace(self.NODE_COMMENT, "ok",
                            elapsed_ms=round((time.perf_counter() - t6) * 1000))
        except Exception:
            self._add_trace(self.NODE_COMMENT, "error",
                            elapsed_ms=round((time.perf_counter() - t6) * 1000))

        total_ms = (time.perf_counter() - t0) * 1000
        print(f"  [task-agent] analyze total={total_ms:.0f}ms")

        # 注入 trace 到返回体
        draft._trace = self._pop_trace()
        draft._total_ms = total_ms
        return draft

    # ============================================================
    # analyze_stream（SSE 流式）
    # ============================================================

    async def analyze_stream(
        self, request: TaskAnalyzeRequest
    ) -> AsyncGenerator[dict, None]:
        """流式分析工单 → SSE 逐 token 输出"""
        t0 = time.perf_counter()
        self._pop_trace()  # 清理上次请求的残留
        await self._ensure_clients()

        # 1. 加载上下文
        yield {"event": "status", "data": {"stage": "loading_context"}}
        t1 = time.perf_counter()
        context = await self._load_task_context(request.task_id)
        self._add_trace(self.NODE_LOAD_CONTEXT, "ok",
                        input={"task_id": request.task_id},
                        output={"has_title": bool(context.title), "has_problem_summary": bool(context.problem_summary),
                                "hypotheses_count": len(context.hypotheses)},
                        elapsed_ms=round((time.perf_counter() - t1) * 1000))

        # 2. 三路分析
        yield {"event": "status", "data": {"stage": "retrieving"}}
        t2 = time.perf_counter()
        retrieval_results = await self._run_analysis(context)
        self._add_trace(self.NODE_RETRIEVE, "ok",
                        input={"query": self._build_query(context)},
                        output={"troubleshooting_len": len(retrieval_results.get("troubleshooting", "")),
                                "history_len": len(retrieval_results.get("history", ""))},
                        elapsed_ms=round((time.perf_counter() - t2) * 1000))

        # 3. 构建 Prompt + 流式生成
        yield {"event": "status", "data": {"stage": "generating"}}
        t3 = time.perf_counter()
        prompt = self._build_prompt(context, retrieval_results)
        self._add_trace(self.NODE_BUILD_PROMPT, "ok",
                        input={"prompt_chars": len(prompt)},
                        elapsed_ms=round((time.perf_counter() - t3) * 1000))

        raw_tokens: list[str] = []
        t_llm = time.perf_counter()
        t_first = None

        try:
            async for token in self._llm_client.stream(
                prompt=prompt,
                system_prompt=TASK_AGENT_SYSTEM_PROMPT,
                max_tokens=1500,
                temperature=0.3,
            ):
                raw_tokens.append(token)
                if t_first is None:
                    t_first = time.perf_counter()
                    first_ms = round((t_first - t_llm) * 1000)
                    yield {"event": "first_token", "data": {"ms": first_ms}}
                yield {"event": "token", "data": token}
            self._add_trace(self.NODE_LLM, "ok",
                            input={"model": self._llm_client.model, "max_tokens": 1500},
                            output={"token_count": len(raw_tokens), "first_token_ms": round((t_first or t_llm) - t_llm) * 1000 if t_first else None},
                            elapsed_ms=round((time.perf_counter() - t_llm) * 1000))
        except Exception:
            self._add_trace(self.NODE_LLM, "error",
                            elapsed_ms=round((time.perf_counter() - t_llm) * 1000))
            msg = "AI 分析服务暂时不可用，请稍后重试。"
            yield {"event": "token", "data": msg}
            result = {
                "root_cause_analysis": "", "suggested_actions": [], "references": [],
                "confidence": 0, "needs_more_info": True,
                "_trace": self._pop_trace(),
            }
            yield {"event": "result", "data": result}
            return

        # 4. 解析 + 保存上下文
        t5 = time.perf_counter()
        raw = "".join(raw_tokens)
        draft, parse_status = self._parse_solution_with_status(raw)
        self._add_trace(self.NODE_PARSE, parse_status,
                        output={"confidence": draft.confidence, "actions_count": len(draft.suggested_actions)},
                        elapsed_ms=round((time.perf_counter() - t5) * 1000))

        t6 = time.perf_counter()
        await self._save_analysis_context(request.session_id, context, draft)
        self._add_trace(self.NODE_MEMORY, "ok",
                        elapsed_ms=round((time.perf_counter() - t6) * 1000))

        # 5. 返回结构化结果（含 trace）
        result_data = draft.model_dump()
        result_data["attachment_analysis"] = retrieval_results.get("attachment_analysis", {})
        total_ms = round((time.perf_counter() - t0) * 1000)
        result_data["_trace"] = self._pop_trace()
        result_data["_total_ms"] = total_ms
        yield {"event": "result", "data": result_data}

        yield {"event": "done", "data": {"total_ms": total_ms}}


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

        # 2. 附件分析（能力一：日志走 LogSubAgent，其他走 parse_attachments）
        t2 = time.perf_counter()
        att_has_logs = False
        att_log_summary = ""
        log_sub_result = None
        try:
            if context.attachments:
                # 2a. 找到日志文件 → 用 LogSubAgent 多轮推理
                log_paths = []
                for att in context.attachments:
                    if not isinstance(att, dict):
                        continue
                    path = att.get("path") or att.get("url") or ""
                    name = (att.get("filename") or att.get("name") or "").lower()
                    if path and (name.endswith((".log", ".txt")) or "log" in name):
                        log_paths.append(path)

                if log_paths:
                    from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import LogSubAgent
                    task_ctx = {
                        "title": context.title,
                        "description": context.description,
                        "problem_summary": context.problem_summary,
                        "hypotheses": context.hypotheses,
                        "ruled_out": context.ruled_out,
                        "robot_type": context.robot_type,
                        "fault_code": context.fault_code,
                        "collected_info": context.collected_info,
                    }
                    # 取第一个日志文件（后续可扩展到多个日志文件的合并分析）
                    sub = LogSubAgent(log_paths[0])
                    log_sub_result = await sub.analyze(task_ctx)
                    if log_sub_result.conclusion:
                        att_has_logs = True
                        att_log_summary = log_sub_result.to_prompt_text()
                    self._add_trace(self.NODE_ATTACHMENT, "ok",
                                    output={"has_logs": att_has_logs,
                                            "sub_rounds": log_sub_result.queries_made,
                                            "evidence_count": len(log_sub_result.evidence)},
                                    elapsed_ms=round((time.perf_counter() - t2) * 1000))

                # 2b. 非日志附件 → 旧 parser（图片/ZIP/文件夹等）
                non_log_atts = [a for a in context.attachments
                                if not ((a.get("filename") or a.get("name") or "").lower().endswith((".log", ".txt")))]
                if non_log_atts and not att_has_logs:
                    from ai.agents.AiTaskPlatform.attachments.parser import parse_attachments
                    att_analysis = await parse_attachments(non_log_atts)
                    att_has_logs = att_has_logs or att_analysis.has_logs
                    if att_analysis.log_summary and not att_log_summary:
                        att_log_summary = att_analysis.log_summary[:500]
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
            attachment_analysis=att_text,
            historical_solutions=hist_text,
        )

        raw = await self._llm_client.complete(
            prompt=prompt, system_prompt=DIAGNOSE_SYSTEM_PROMPT,
            max_tokens=1500, temperature=0.3,
        )
        self._add_trace(self.NODE_LLM, "ok",
                        input={"model": self._llm_client.model, "prompt_chars": len(prompt)},
                        output={"response_chars": len(raw)},
                        elapsed_ms=round((time.perf_counter() - t4) * 1000))

        # 5. 解析
        t5 = time.perf_counter()
        draft, parse_status = self._parse_solution_with_status(raw)
        self._add_trace(self.NODE_PARSE, parse_status,
                        output={"confidence": draft.confidence},
                        elapsed_ms=round((time.perf_counter() - t5) * 1000))

        total_ms = round((time.perf_counter() - t0) * 1000)

        return {
            "task_id": task_id,
            "root_cause_analysis": draft.root_cause_analysis,
            "suggested_actions": draft.suggested_actions,
            "references": draft.references,
            "confidence": draft.confidence,
            "needs_more_info": draft.needs_more_info,
            "attachment_analysis": {"has_logs": att_has_logs, "summary": att_log_summary},
            "history_found": hist_found,
            "_trace": self._pop_trace(),
            "_total_ms": total_ms,
        }

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

        # 3. 按需调日志子Agent / 附件分析 / 历史工单
        facultative = ""
        att_keywords = ["日志", "附件", "图片", "log", "file", "image", "zip"]
        hist_keywords = ["历史", "类似", "之前", "案例", "参考"]

        if query and any(kw in query.lower() for kw in att_keywords):
            try:
                if ctx.attachments:
                    # 3a. 日志文件 → LogSubAgent 多轮推理
                    log_paths = []
                    for att in ctx.attachments:
                        if not isinstance(att, dict):
                            continue
                        path = att.get("path") or att.get("url") or ""
                        name = (att.get("filename") or att.get("name") or "").lower()
                        if path and (name.endswith((".log", ".txt")) or "log" in name):
                            log_paths.append(path)

                    if log_paths:
                        from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import LogSubAgent
                        task_ctx = {
                            "title": ctx.title, "description": ctx.description,
                            "problem_summary": ctx.problem_summary,
                            "hypotheses": ctx.hypotheses, "ruled_out": ctx.ruled_out,
                            "robot_type": ctx.robot_type, "fault_code": ctx.fault_code,
                            "collected_info": ctx.collected_info,
                        }
                        sub = LogSubAgent(log_paths[0])
                        log_result = await sub.analyze(task_ctx, user_question=query)
                        if log_result.conclusion:
                            facultative += f"\n[日志子Agent分析（{log_result.queries_made}轮查询）]\n{log_result.to_prompt_text()}\n"

                    # 3b. 非日志附件 → 旧 parser
                    non_log = [a for a in ctx.attachments
                               if not ((a.get("filename") or a.get("name") or "").lower().endswith((".log", ".txt")))]
                    if non_log and not facultative:
                        from ai.agents.AiTaskPlatform.attachments.parser import parse_attachments
                        att = await parse_attachments(non_log)
                        if att.has_logs:
                            facultative += f"\n[附件分析]\n{att.log_summary[:500]}\n"
            except Exception:
                pass

        if query and any(kw in query.lower() for kw in hist_keywords):
            try:
                query_text = self._build_query(ctx)
                hist = await self._retrieve_task_resolutions(query_text)
                if hist and "无" not in hist[:10]:
                    facultative += f"\n[历史相似工单]\n{hist[:500]}\n"
            except Exception:
                pass

        # 4. LLM
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
            prompt=prompt, system_prompt=DISCUSS_SYSTEM_PROMPT,
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

    @staticmethod
    def _add_diagnosis_comment_short(task_id: int, content: str) -> bool:
        """简短回复写入 task_comments（用于 @AI 讨论/摘要）"""
        from app.models.task import TaskComment
        from app.core.database import SessionLocal
        db = SessionLocal()
        try:
            comment = TaskComment(task_id=task_id, content=content,
                                  created_by="AI任务助手", is_public=True)
            db.add(comment)
            db.commit()
            return True
        finally:
            db.close()

    # ============================================================
    # summarize — 讨论摘要
    # ============================================================

    async def summarize(
        self, task_id: str, title: str = "", description: str = "",
        diagnosis_summary: str = "", discussion_history: list = None,
        previous_summary: str = "",
    ) -> dict:
        """纯摘要生成服务：后端传入工单信息+讨论记录 → LLM 生成摘要 → 返回。

        如果 previous_summary 非空，做增量总结（融入新讨论到已有摘要）。
        不做 DB 操作、不写 task_comments。后端决定触发时机和数据来源。
        """
        t0 = time.perf_counter()
        self._pop_trace()
        await self._ensure_clients()

        # 组装讨论文本
        history_items = discussion_history or []
        history_lines = []
        for item in history_items[-20:]:
            author = item.get("author", item.get("created_by", "?"))
            content = str(item.get("content", ""))[:200]
            history_lines.append(f"[{author}] {content}")
        history_text = "\n".join(history_lines) if history_lines else "（暂无讨论）"

        if previous_summary:
            # 增量模式：只需上次摘要 + 新增讨论
            prompt = SUMMARIZE_INCREMENTAL_TEMPLATE.format(
                previous_summary=previous_summary,
                discussion_history=history_text,
            )
        else:
            # 首次模式：完整工单上下文
            prompt = SUMMARIZE_FULL_TEMPLATE.format(
                title=title or f"工单 #{task_id}",
                description=description or "",
                diagnosis_summary=diagnosis_summary or "无",
                discussion_history=history_text,
            )
        t_llm = time.perf_counter()
        summary = await self._llm_client.complete(
            prompt=prompt, system_prompt=SUMMARIZE_SYSTEM_PROMPT,
            max_tokens=300, temperature=0.3,
        )
        self._add_trace(self.NODE_LLM, "ok",
                        output={"summary_chars": len(summary)},
                        elapsed_ms=round((time.perf_counter() - t_llm) * 1000))

        total_ms = round((time.perf_counter() - t0) * 1000)
        return {
            "task_id": task_id,
            "summary": summary.strip(),
            "_trace": self._pop_trace(),
            "_total_ms": total_ms,
        }

    # submit — 方案提交
    # ============================================================

    async def submit(
        self, task_id: str, session_id: str, draft, resolution: str = "resolved"
    ) -> dict:
        """确认方案 → Qdrant 回写 + tasks 表状态更新。"""
        t0 = time.perf_counter()
        self._pop_trace()
        await self._ensure_clients()
        result = {"task_id": task_id, "solution_indexed": False, "ticket_updated": False}

        solution_text = f"根因: {getattr(draft, 'root_cause_analysis', '')}\n步骤: {'; '.join(getattr(draft, 'suggested_actions', []))}"

        # 1. Qdrant 回写
        t_qdrant = time.perf_counter()
        try:
            await self._index_solution(task_id, solution_text, draft)
            result["solution_indexed"] = True
            self._add_trace(self.NODE_SUBMIT + "_qdrant", "ok",
                            elapsed_ms=round((time.perf_counter() - t_qdrant) * 1000))
        except Exception as e:
            self._add_trace(self.NODE_SUBMIT + "_qdrant", "error",
                            input={"error": str(e)},
                            elapsed_ms=round((time.perf_counter() - t_qdrant) * 1000))

        # 2. tasks 表更新
        t_db = time.perf_counter()
        try:
            from ai.core.task_adapter import update_task_resolution
            draft_dict = getattr(draft, 'model_dump', lambda: {})() if hasattr(draft, 'model_dump') else {}
            ok = update_task_resolution(task_id, draft_dict, resolution)
            result["ticket_updated"] = ok
            self._add_trace(self.NODE_SUBMIT + "_db", "ok",
                            elapsed_ms=round((time.perf_counter() - t_db) * 1000))
        except Exception as e:
            self._add_trace(self.NODE_SUBMIT + "_db", "error",
                            input={"error": str(e)},
                            elapsed_ms=round((time.perf_counter() - t_db) * 1000))

        result["_trace"] = self._pop_trace()
        result["_total_ms"] = round((time.perf_counter() - t0) * 1000)
        return {"code": 0, "data": result}

    async def _load_task_context(self, task_id: str) -> TaskContext:
        """从 tasks 表读取工单上下文（source='ai' 任务）。

        AI 专属字段（diagnosis/robot_type/fault_code 等）存于 metadata_info。
        priority 由适配层反向映射回中文，保持 LLM prompt 输入不变。
        """
        ctx = TaskContext(task_id=task_id)

        try:
            from ai.core.task_adapter import load_task_context_dict
            d = load_task_context_dict(task_id)
            if d:
                ctx.title = d.get("title", "")
                ctx.description = d.get("description", "")
                ctx.task_type = d.get("type", "problem") or "problem"
                ctx.priority = d.get("priority", "中") or "中"
                ctx.status = d.get("status", "pending") or "pending"
                ctx.source = d.get("source", "ai") or "ai"
                ctx.attachments = d.get("attachments") or []
                ctx.robot_type = d.get("robot_type", "")
                ctx.fault_code = d.get("fault_code", "")
                ctx.location = d.get("location", "")

                # diagnosis JSON — 提单 Agent 的诊断结果（核心材料）
                ctx.problem_summary = d.get("problem_summary", "")
                ctx.hypotheses = d.get("hypotheses") or []
                ctx.ruled_out = d.get("ruled_out") or []
                ctx.collected_info = d.get("collected_info") or {}
                ctx.diagnosis_rounds = d.get("diagnosis_rounds", 0)
        except Exception as e:
            print(f"  [task-agent] Failed to load task {task_id}: {e}")

        return ctx

    # ============================================================
    # 私有：三路分析
    # ============================================================

    async def _run_analysis(self, context: TaskContext) -> dict:
        """三路并行分析：排查树结论 + 历史工单方案 + 附件解析"""
        results = {"attachment_analysis": {}}

        # 构建检索查询文本
        query_text = context.problem_summary or context.description
        if context.hypotheses:
            query_text += " " + " ".join(context.hypotheses)
        if context.fault_code:
            query_text += " " + context.fault_code
        if context.robot_type:
            query_text += " " + context.robot_type

        # 并行执行三路
        troubleshooting_task = self._retrieve_troubleshooting_conclusions(query_text)
        history_task = self._retrieve_task_resolutions(query_text)
        attachment_task = self._parse_attachments(context.attachments)

        try:
            results["troubleshooting"], results["history"], results["attachment_analysis"] = (
                await asyncio.gather(troubleshooting_task, history_task, attachment_task)
            )
        except Exception as e:
            print(f"  [task-agent] Partial analysis failure: {e}")
            if "troubleshooting" not in results:
                results["troubleshooting"] = "（检索暂不可用）"
            if "history" not in results:
                results["history"] = "（检索暂不可用）"

        return results

    async def _retrieve_troubleshooting_conclusions(self, query: str) -> str:
        """检索排查树，只取结论节点（根因 + 方案）。"""
        try:
            results = await self._retriever.retrieve_troubleshooting(query, top_k=3)
            if not results:
                return "（无匹配的排查树结论）"

            lines = []
            for i, r in enumerate(results, 1):
                title = r.title or ""
                content = r.content or ""
                if content.strip():
                    lines.append(f"排查树 {i}：{title}\n{content}\n---")
            return "\n".join(lines) if lines else "（无匹配的排查树结论）"
        except Exception:
            return "（排查树检索失败）"

    async def _retrieve_task_resolutions(self, query: str) -> str:
        """检索历史工单方案（Qdrant task_resolutions collection）。"""
        if not hasattr(self._retriever, "retrieve_task_resolutions"):
            return "（task_resolutions collection 尚未建立）"

        try:
            results = await self._retriever.retrieve_task_resolutions(query, top_k=3)
            if not results:
                return "（无相似的历史工单方案）"

            lines = []
            for i, r in enumerate(results, 1):
                title = r.title or f"工单 #{r.id}"
                content = r.content or ""
                if content.strip():
                    lines.append(f"历史工单 {i}：{title}\n{content}\n---")
            return "\n".join(lines) if lines else "（无相似的历史工单方案）"
        except Exception:
            return "（历史方案检索失败）"

    async def _parse_attachments(self, attachments: list) -> AttachmentAnalysis:
        """解析附件：日志 → 关键错误 + 回放 → 路径分析。

        第一期只做 txt/log 文件的正则解析，回放后续补。
        """
        result = AttachmentAnalysis()
        if not attachments:
            return result

        for att in attachments:
            filename = att.get("filename") or att.get("name", "")
            if filename.lower().endswith((".txt", ".log", ".csv")):
                result.has_logs = True
                try:
                    # 从附件路径读取内容（本地文件或远程 URL）
                    content = await self._read_attachment_content(att)
                    if content:
                        result.log_summary = self._extract_log_errors(content)
                except Exception:
                    pass
            elif "replay" in filename.lower() or "回放" in filename:
                result.has_replay = True
                # 回放解析后续实现

        return result

    async def _read_attachment_content(self, att: dict) -> str:
        """读取附件文本内容（≤100KB）。"""
        path = att.get("path") or att.get("url", "")
        if not path:
            return ""

        try:
            if path.startswith("http"):
                async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                    resp = await client.get(path)
                    if resp.status_code == 200:
                        text = resp.text
                        return text[:100_000]  # 截断
            else:
                from pathlib import Path as _Path
                local = _Path(path)
                if local.exists():
                    return local.read_text(encoding="utf-8", errors="replace")[:100_000]
        except Exception:
            pass

        return ""

    @staticmethod
    def _extract_log_errors(text: str) -> str:
        """从日志文本提取 ERROR/WARN 行 + 时间线上下文。

        Returns:
            摘要文本 (≤2000 chars)
        """
        lines = text.split("\n")
        error_lines = []
        for line in lines:
            upper = line.upper()
            if any(kw in upper for kw in ("ERROR", "WARN", "EXCEPTION", "FAIL", "FATAL")):
                error_lines.append(line.strip()[:200])

        if not error_lines:
            # 没有明显错误 → 返回首尾行作为时间线参考
            first_ts = next((l for l in lines if len(l) > 20), "")
            last_ts = next((l for l in reversed(lines) if len(l) > 20), "")
            return f"日志 {len(lines)} 行，无明显错误。首行: {first_ts[:120]}, 尾行: {last_ts[:120]}"

        summary_lines = [
            f"日志 {len(lines)} 行，提取到 {len(error_lines)} 条异常："
        ] + error_lines[:20]
        return "\n".join(summary_lines)[:2000]

    # ============================================================
    # 私有：Prompt 构建 + 解析
    # ============================================================

    def _build_prompt(self, context: TaskContext, retrieval: dict) -> str:
        """组装用户 Prompt 模板"""
        # 故障信息行
        fault_parts = []
        if context.fault_code:
            fault_parts.append(f"故障码: {context.fault_code}")
        if context.robot_type:
            fault_parts.append(f"车型: {context.robot_type}")
        if context.location:
            fault_parts.append(f"位置: {context.location}")
        fault_info = "\n".join(fault_parts) if fault_parts else "（无特殊故障信息）"

        # 附件分析摘要
        att = retrieval.get("attachment_analysis", {})
        # 兼容 Pydantic 对象和 dict
        att_dict = att.model_dump() if hasattr(att, 'model_dump') else (att or {})
        if att_dict.get("has_logs") or att_dict.get("has_replay"):
            attachment_text = json.dumps(
                {k: v for k, v in att_dict.items() if v},
                ensure_ascii=False, indent=2
            )
        else:
            attachment_text = "（无附件或无可解析内容）"

        return USER_PROMPT_TEMPLATE.format(
            title=context.title,
            description=context.description,
            task_type=context.task_type,
            priority=context.priority,
            source=context.source or "unknown",
            problem_summary=context.problem_summary or "（提单 Agent 未提供）",
            hypotheses="、".join(context.hypotheses) if context.hypotheses else "（无）",
            ruled_out="、".join(context.ruled_out) if context.ruled_out else "（无）",
            collected_info=json.dumps(context.collected_info, ensure_ascii=False)
            if context.collected_info else "（无）",
            rounds=context.diagnosis_rounds,
            fault_info=fault_info,
            troubleshooting_conclusions=retrieval.get("troubleshooting", "（排查树检索未执行）"),
            historical_solutions=retrieval.get("history", "（历史方案检索未执行）"),
            attachment_analysis=attachment_text,
        )

    @staticmethod
    def _parse_solution(raw: str) -> SolutionDraft:
        """从 LLM 原始输出解析 SolutionDraft JSON"""
        draft, _ = AiTaskAgent._parse_solution_with_status(raw)
        return draft

    @staticmethod
    def _parse_solution_with_status(raw: str) -> tuple[SolutionDraft, str]:
        """解析 SolutionDraft JSON，同时返回状态 (ok/json_fail)"""
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if json_match:
            try:
                data = json.loads(json_match.group())
                return SolutionDraft(
                    root_cause_analysis=data.get("root_cause_analysis", ""),
                    suggested_actions=data.get("suggested_actions", []),
                    references=data.get("references", []),
                    confidence=float(data.get("confidence", 0.0)),
                    needs_more_info=bool(data.get("needs_more_info", False)),
                ), "ok"
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        return SolutionDraft(
            root_cause_analysis=raw.strip(),
            suggested_actions=[], references=[],
            confidence=0.0, needs_more_info=True,
        ), "json_fail"

    @staticmethod
    def _build_query(context: TaskContext) -> str:
        """构建检索查询文本"""
        parts = []
        if context.problem_summary:
            parts.append(context.problem_summary)
        elif context.description:
            parts.append(context.description)
        if context.hypotheses:
            parts.append(" ".join(context.hypotheses))
        if context.fault_code:
            parts.append(context.fault_code)
        if context.robot_type:
            parts.append(context.robot_type)
        return " ".join(parts) if parts else context.description

    # ============================================================
    # 私有：记忆 + 索引
    # ============================================================

    async def _save_analysis_context(
        self, session_id: str, context: TaskContext, draft: SolutionDraft
    ) -> None:
        """保存分析上下文到 Redis 记忆（供多轮编辑）"""
        try:
            await self._memory.add_turn(
                session_id, "user",
                f"分析工单 #{context.task_id}: {context.title}"
            )
            await self._memory.add_turn(
                session_id, "assistant",
                json.dumps(draft.model_dump(), ensure_ascii=False)
            )
        except Exception:
            pass

    async def _index_solution(
        self, task_id: str, solution_text: str, draft: SolutionDraft
    ) -> None:
        """向量化方案 → 写入 Qdrant task_resolutions collection"""
        try:
            from ai.core.task_adapter import load_task_context_dict
            d = load_task_context_dict(task_id)
            title = d.get("title", f"工单 #{task_id}") or f"工单 #{task_id}"
            fault_code = d.get("fault_code", "")
            robot_type = d.get("robot_type", "")
            problem_summary = d.get("problem_summary", "")

            await self._retriever.index_task_resolution(
                task_id=task_id,
                title=title,
                root_cause=draft.root_cause_analysis,
                solution_steps="；".join(draft.suggested_actions),
                engineer_note=draft.references[0] if draft.references else "",
                fault_code=fault_code,
                robot_type=robot_type,
                problem_summary=problem_summary,
            )
        except Exception as e:
            print(f"  [task-agent] Solution index failed: {e}")

    @staticmethod
    def _add_diagnosis_comment(task_id: int, draft: "SolutionDraft", created_by: str = "AI任务助手") -> bool:
        """将 AI 诊断结果写入 task_comments 表。

        当前 created_by 固定为 \"AI任务助手\"。
        TODO: 后续改为提单人的用户名（从工单 created_by 字段获取）。
        """
        from app.models.task import TaskComment
        from app.core.database import SessionLocal
        content_parts = [
            f"## AI 诊断结果",
            f"",
            f"**根因分析**：{draft.root_cause_analysis}",
            f"",
            f"**建议步骤**：",
        ]
        for i, action in enumerate(draft.suggested_actions, 1):
            content_parts.append(f"{i}. {action}")
        if draft.references:
            content_parts.append(f"")
            content_parts.append(f"**参考来源**：")
            for ref in draft.references:
                content_parts.append(f"- {ref}")
        content_parts.append(f"")
        content_parts.append(f"置信度：{draft.confidence:.0%}")

        db = SessionLocal()
        try:
            comment = TaskComment(
                task_id=task_id,
                content="\n".join(content_parts),
                created_by=created_by,
                is_public=True,
            )
            db.add(comment)
            db.commit()
            return True
        except Exception as e:
            print(f"  [task-agent] Diagnosis comment failed: {e}")
            return False
        finally:
            db.close()


# ============================================================
# 全局单例
# ============================================================

_agent: Optional[AiTaskAgent] = None
_agent_lock = asyncio.Lock()


async def get_task_agent() -> AiTaskAgent:
    """获取任务 Agent 单例"""
    global _agent
    if _agent is None:
        async with _agent_lock:
            if _agent is None:
                _agent = AiTaskAgent()
    return _agent
