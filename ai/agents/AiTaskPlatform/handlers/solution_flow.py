"""解决方案流程（方案生成/流式/提交）— 从 pipeline.py 拆分出的 Mixin

含 AiTaskAgent 的方法（保持 self.xxx 调用不变，仅拆分文件）：
  - analyze: 非流式分析工单 → SolutionDraft
  - submit: 确认方案 → Qdrant 回写 + tasks 表更新
"""

import time

from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.schemas import TaskAnalyzeRequest, SolutionDraft, ResolvedRootCause
from ai.agents.AiTaskPlatform.prompts import TASK_AGENT_SYSTEM_PROMPT

logger = get_logger("TASK_AGENT")


# P1 结构化根因：把方案草稿提炼成结构化字段（submit 时一次 LLM 小调用，temp=0）
_STRUCT_ROOT_SYSTEM = (
    "你是工单根因结构化助手。把一份方案草稿提炼成结构化 JSON，只输出 JSON。"
)
_STRUCT_ROOT_USER = """根据方案草稿提炼根因的结构化信息，只输出 JSON（无其他文字）。

## 方案草稿
{root_cause}

## 建议步骤
{actions}

## 输出 JSON
{{
  "symptom": "现象/症状（一句话）",
  "error_codes": ["相关错误码/异常短语，最多5个，无则[]"],
  "root_cause_type": "版本缺陷|配置错误|环境问题|竞态|硬件|未知",
  "severity": "高|中|低|未知",
  "is_common_bug": true或false
}}
注意：信息不足以判断的类型填"未知"；不要编造错误码。"""


def _parse_struct_root(raw: str) -> ResolvedRootCause:
    """从 LLM 输出解析 ResolvedRootCause（健壮 JSON，失败回退 safe 默认）。"""
    import json, re
    if not raw:
        return ResolvedRootCause()
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        text = m.group(1)
    else:
        s, e = text.find("{"), text.rfind("}")
        if s != -1 and e > s:
            text = text[s : e + 1]
    try:
        data = json.loads(text)
    except Exception:
        return ResolvedRootCause()
    if not isinstance(data, dict):
        return ResolvedRootCause()
    codes = data.get("error_codes") or []
    if not isinstance(codes, list):
        codes = []
    # root_cause_type 归一化为规范值（unknown 为兜底，避免中英文混用影响下游过滤）
    rct = str(data.get("root_cause_type", "")).strip()
    rct = "未知" if rct in ("", "unknown", "不清楚", "无法判断") else rct
    if rct not in ("版本缺陷", "配置错误", "环境问题", "竞态", "硬件", "未知"):
        rct = "未知"
    rct = "unknown" if rct == "未知" else rct
    sev = str(data.get("severity", "")).strip()
    sev = "未知" if sev in ("", "unknown", "不清楚", "无法判断") else sev
    if sev not in ("高", "中", "低", "未知"):
        sev = "未知"
    sev = "unknown" if sev == "未知" else sev
    return ResolvedRootCause(
        symptom=str(data.get("symptom", ""))[:300],
        error_codes=[str(c) for c in codes][:5],
        root_cause_type=rct,
        severity=sev,
        is_common_bug=bool(data.get("is_common_bug", False)),
    )


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
    # 结构化根因提炼（P1）— submit 前调用一次，供 Qdrant 写入结构化字段
    # ============================================================
    async def _extract_structured_root_cause(self, draft) -> dict:
        """把方案草稿提炼成结构化根因 dict（供 _index_solution 写入）。

        一次小 LLM 调用（temp=0）；失败/解析失败回退 safe 默认，绝不阻断 submit。
        """
        try:
            root_cause = getattr(draft, "root_cause_analysis", "") or ""
            actions = "；".join(getattr(draft, "suggested_actions", []) or [])
            if not root_cause:
                return {}
            raw = await self._llm_client.complete(
                prompt=_STRUCT_ROOT_USER.format(root_cause=root_cause[:1500], actions=actions[:800]),
                system_prompt=_STRUCT_ROOT_SYSTEM,
                max_tokens=200,
                temperature=0.0,
            )
        except Exception as e:
            logger.warning(f"[solution] 结构化根因提炼调用失败，回退默认: {e}")
            return {}
        sr = _parse_struct_root(raw)
        if not sr.symptom and not sr.error_codes and sr.root_cause_type == "未知":
            return {}  # 解析失败 → 不写结构化字段
        extra = sr.to_payload()
        # 补充根因/方案（保留给检索可参考）
        extra["root_cause"] = getattr(draft, "root_cause_analysis", "") or ""
        return extra

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

        # P1：结构化根因提炼（temp=0 小调用，失败回退不阻断）
        structured = await self._extract_structured_root_cause(draft)

        # 1. Qdrant 回写
        t_qdrant = time.perf_counter()
        try:
            await self._index_solution(task_id, solution_text, draft, structured=structured)
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
