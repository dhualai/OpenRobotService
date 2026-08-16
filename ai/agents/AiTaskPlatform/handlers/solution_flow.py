"""解决方案流程（方案生成/流式/提交）— 从 pipeline.py 拆分出的 Mixin

含 AiTaskAgent 的方法（保持 self.xxx 调用不变，仅拆分文件）：
  - analyze: 非流式分析工单 → SolutionDraft
  - submit: 确认方案 → Qdrant 回写 + tasks 表更新
"""

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
