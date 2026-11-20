"""解决方案流程（方案生成）— 从 pipeline.py 拆分出的 Mixin

含 AiTaskAgent 的方法（保持 self.xxx 调用不变，仅拆分文件）：
  - analyze: 非流式分析工单 → SolutionDraft

说明：submit（方案提交）接口前端未启用，已废弃移除；
解决方式统一由「结束工单」流程写入 metadata_info.resolution_summary。
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
