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
    TASK_AGENT_SYSTEM_PROMPT,
    USER_PROMPT_TEMPLATE,
    TASK_CHAT_SYSTEM_PROMPT,
)


# ============================================================
# 核心类
# ============================================================

class AiTaskAgent:
    """任务 Agent：分析工单 → 生成解决方案草稿"""

    # ── 可追踪的流程节点（供测试 Agent 对照）──
    NODE_OVERHEAD = "overhead"          # 端点路由 + 客户端初始化
    NODE_LOAD_CONTEXT = "load_context"  # 加载工单上下文（SQLAlchemy 读 tickets）
    NODE_RETRIEVE = "retrieve"          # 三路并行分析
    NODE_ATTACHMENT = "attachment"      # 附件解析（日志/回放）
    NODE_BUILD_PROMPT = "build_prompt"  # Prompt 构建
    NODE_LLM = "llm"                   # LLM 调用（DeepSeek API）
    NODE_PARSE = "parse"               # 结果解析（JSON→SolutionDraft）
    NODE_MEMORY = "memory"             # 记忆保存（Redis）
    NODE_COMMENT = "comment"           # 诊断结果写入 task_comments
    NODE_SUBMIT = "submit"             # 方案提交（tasks 表 + Qdrant 回写）

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
    # chat（自由问答，无 taskId 或通用技术问答）
    # ============================================================

    async def chat(
        self, session_id: str, query: str,
        username: str = "", token: str = "",
    ) -> str:
        """v2.0 自由对话：感知用户所有工单 + 诊断状态。

        Args:
            username: 当前工程师用户名（从 auth store 传入）
            token: 用户 JWT token（用于调后端 API 鉴权）
        """
        t0 = time.perf_counter()
        self._pop_trace()
        await self._ensure_clients()

        # 1. 加载用户工单列表
        tasks_summary = await self._fetch_user_tasks_summary(username, token)

        # 2. 对话上下文
        conversation = ""
        try:
            memory = await self._memory.get_memory(session_id)
            turns = memory.turns[-8:] if len(memory.turns) > 8 else memory.turns
            conversation = "\n".join(
                f"{'用户' if t['role'] == 'user' else '助手'}：{t['content']}"
                for t in turns
            )
        except Exception:
            pass

        # 3. 构建 prompt
        t_prompt = time.perf_counter()
        prompt = (
            f"## 对话历史\n{conversation}\n\n"
            f"## 用户消息\n{query}\n\n"
            f"## 当前用户的工单\n{tasks_summary}"
        )
        self._add_trace(self.NODE_BUILD_PROMPT, "ok",
                        input={"prompt_chars": len(prompt), "task_count": tasks_summary.count("#")},
                        elapsed_ms=round((time.perf_counter() - t_prompt) * 1000))

        # 4. LLM
        t_llm = time.perf_counter()
        response = await self._llm_client.complete(
            prompt=prompt,
            system_prompt=TASK_CHAT_SYSTEM_PROMPT,
            max_tokens=1500,
            temperature=0.5,
        )
        self._add_trace(self.NODE_LLM, "ok",
                        input={"model": self._llm_client.model},
                        output={"response_chars": len(response)},
                        elapsed_ms=round((time.perf_counter() - t_llm) * 1000))

        # 5. 记忆
        t_mem = time.perf_counter()
        try:
            await self._memory.add_turn(session_id, "user", query)
            await self._memory.add_turn(session_id, "assistant", response)
            self._add_trace(self.NODE_MEMORY, "ok",
                            elapsed_ms=round((time.perf_counter() - t_mem) * 1000))
        except Exception:
            self._add_trace(self.NODE_MEMORY, "error",
                            elapsed_ms=round((time.perf_counter() - t_mem) * 1000))

        return response

    async def chat_stream(
        self, session_id: str, query: str,
        username: str = "", token: str = "",
    ):
        """v2.0 流式自由对话：感知用户所有工单"""
        import time as _time
        t0 = _time.perf_counter()
        self._pop_trace()
        await self._ensure_clients()

        # 1. 加载用户工单列表
        tasks_summary = await self._fetch_user_tasks_summary(username, token)

        # 2. 对话上下文
        conversation = ""
        try:
            memory = await self._memory.get_memory(session_id)
            turns = memory.turns[-8:] if len(memory.turns) > 8 else memory.turns
            conversation = "\n".join(
                f"{'用户' if t['role'] == 'user' else '助手'}：{t['content']}"
                for t in turns
            )
        except Exception:
            pass

        t_prompt = _time.perf_counter()
        prompt = (
            f"## 对话历史\n{conversation}\n\n"
            f"## 用户消息\n{query}\n\n"
            f"## 当前用户的工单\n{tasks_summary}"
        )
        self._add_trace(self.NODE_BUILD_PROMPT, "ok",
                        input={"prompt_chars": len(prompt), "task_count": tasks_summary.count("#")},
                        elapsed_ms=round((_time.perf_counter() - t_prompt) * 1000))

        yield {"event": "status", "data": {"stage": "chatting"}}
        t_llm = _time.perf_counter()
        t_first = None
        acc_tokens: list[str] = []

        try:
            async for token in self._llm_client.stream(
                prompt=prompt,
                system_prompt=TASK_CHAT_SYSTEM_PROMPT,
                max_tokens=1500,
                temperature=0.5,
            ):
                acc_tokens.append(token)
                if t_first is None:
                    t_first = _time.perf_counter()
                    yield {"event": "first_token", "data": {"ms": round((t_first - t_llm) * 1000)}}
                yield {"event": "token", "data": token}
            self._add_trace(self.NODE_LLM, "ok",
                            input={"model": self._llm_client.model},
                            output={"token_count": len(acc_tokens), "response_chars": sum(len(t) for t in acc_tokens)},
                            elapsed_ms=round((_time.perf_counter() - t_llm) * 1000))
        except Exception:
            self._add_trace(self.NODE_LLM, "error",
                            elapsed_ms=round((_time.perf_counter() - t_llm) * 1000))
            yield {"event": "token", "data": "AI 服务暂时不可用，请稍后重试。"}

        # 写入对话记忆
        t_mem = _time.perf_counter()
        full_response = "".join(acc_tokens)
        if full_response:
            try:
                await self._memory.add_turn(session_id, "user", query)
                await self._memory.add_turn(session_id, "assistant", full_response)
                self._add_trace(self.NODE_MEMORY, "ok",
                                elapsed_ms=round((_time.perf_counter() - t_mem) * 1000))
            except Exception:
                self._add_trace(self.NODE_MEMORY, "error",
                                elapsed_ms=round((_time.perf_counter() - t_mem) * 1000))

        total_ms = round((_time.perf_counter() - t0) * 1000)
        yield {"event": "done", "data": {"total_ms": total_ms, "_trace": self._pop_trace()}}

    # ============================================================
    # submit（方案确认 → Qdrant + 后端状态更新）
    # ============================================================

    async def submit(
        self, task_id: str, session_id: str, draft: SolutionDraft, resolution: str = "resolved"
    ) -> dict:
        """确认方案 → Qdrant 回写 + tickets 表状态更新。"""
        t0 = time.perf_counter()
        self._pop_trace()
        await self._ensure_clients()
        result = {"task_id": task_id, "solution_indexed": False, "ticket_updated": False}

        solution_text = (
            f"根因: {draft.root_cause_analysis}\n"
            f"步骤: {'; '.join(draft.suggested_actions)}"
        )

        # 1. Qdrant 回写
        t_qdrant = time.perf_counter()
        try:
            await self._index_solution(task_id, solution_text, draft)
            result["solution_indexed"] = True
            self._add_trace(self.NODE_SUBMIT + "_qdrant", "ok",
                            elapsed_ms=round((time.perf_counter() - t_qdrant) * 1000))
        except Exception as e:
            self._add_trace(self.NODE_SUBMIT + "_qdrant", "error",
                            input={"error": str(e)}, elapsed_ms=round((time.perf_counter() - t_qdrant) * 1000))

        # 2. tasks 表更新（source='ai' 任务）
        t_db = time.perf_counter()
        try:
            from ai.core.task_adapter import update_task_resolution
            ok = update_task_resolution(task_id, draft.model_dump(), resolution)
            result["ticket_updated"] = ok
            self._add_trace(self.NODE_SUBMIT + "_db", "ok",
                            elapsed_ms=round((time.perf_counter() - t_db) * 1000))
        except Exception as e:
            self._add_trace(self.NODE_SUBMIT + "_db", "error",
                            input={"error": str(e)}, elapsed_ms=round((time.perf_counter() - t_db) * 1000))

        result["_trace"] = self._pop_trace()
        result["_total_ms"] = round((time.perf_counter() - t0) * 1000)
        return {"code": 0, "data": result}

    # ============================================================
    # 私有：上下文加载 — 直接从 tickets 表读取（AI 模块自有数据）
    # ============================================================

    async def _fetch_user_tasks_summary(self, username: str, token: str = "") -> str:
        """从业务后端获取当前用户工单 + 诊断状态摘要（带 JWT 鉴权）。

        返回注入 Chat Prompt 的文本：每条工单一行，含优先级/状态/诊断状态。
        无工单时返回"（无待处理工单）"。
        """
        if not username:
            return "（无用户信息，无法获取工单列表）"

        try:
            headers = {}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                resp = await client.get(
                    f"http://127.0.0.1:8400/api/tasks/",
                    params={"assigned_to": username, "size": 50, "status": "in_progress,pending,new"},
                    headers=headers,
                )
                if resp.status_code != 200:
                    return "（工单列表获取失败）"

                data = resp.json()
                items = data.get("items") or data.get("data", {}).get("items", [])
                if not items:
                    return "（无待处理工单）"

                from ai.agents.AiTaskPlatform.diagnosis_service import _is_diagnosed

                lines = []
                for task in items:
                    tid = task.get("id", "")
                    title = task.get("title", "")[:50]
                    priority = task.get("priority", "中")
                    status = task.get("status", "")
                    status_cn = {"new": "新建", "in_progress": "进行中", "pending": "待处理",
                                  "resolved": "已解决", "closed": "已关闭"}.get(status, status)
                    diagnosed = _is_diagnosed(int(tid))
                    diag_mark = "✅已诊断" if diagnosed else "⚠️待诊断"

                    lines.append(f"  #{tid} {title} [{priority}/{status_cn}/{diag_mark}]")

                return "\n".join(lines) if lines else "（无待处理工单）"

        except Exception as e:
            print(f"  [task-agent] Failed to fetch user tasks: {e}")
            return "（工单列表暂时不可用）"

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
