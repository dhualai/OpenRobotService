"""解决方案流程（方案生成/流式/提交）— 从 pipeline.py 拆分出的 Mixin

含 AiTaskAgent 的三个方法（保持 self.xxx 调用不变，仅拆分文件）：
  - analyze: 非流式分析工单 → SolutionDraft
  - analyze_stream: SSE 流式分析
  - submit: 确认方案 → Qdrant 回写 + tasks 表更新
"""

import json
import time

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.schemas import TaskAnalyzeRequest, SolutionDraft
from ai.agents.AiTaskPlatform.prompts import TASK_AGENT_SYSTEM_PROMPT

logger = get_logger("TASK_AGENT")


class SolutionFlow:
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

        # 6. 诊断结果写入 task_comments（U老师评论）
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
        logger.info(f"analyze total={total_ms:.0f}ms")

        # 注入 trace 到返回体
        draft._trace = self._pop_trace()
        draft._total_ms = total_ms
        return draft

    # ============================================================
    # analyze_stream（SSE 流式）
    # ============================================================

    async def analyze_stream(
        self, request: TaskAnalyzeRequest
    ):
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

        raw_tokens: list = []
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
                            output={"token_count": len(raw_tokens),
                                    "first_token_ms": round((t_first or t_llm) - t_llm) * 1000 if t_first else None},
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
