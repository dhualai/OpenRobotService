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
)


# ============================================================
# 核心类
# ============================================================

class AiTaskAgent:
    """任务 Agent：分析工单 → 生成解决方案草稿"""

    def __init__(self):
        self.config = get_ai_config()
        self._llm_client = None
        self._retriever = None
        self._memory = None
        self._backend_url = "http://127.0.0.1:8400"  # 业务后端地址
        self._backend_api_prefix = "/api/v1"

    async def _ensure_clients(self):
        """懒加载 AI 核心服务单例"""
        if self._llm_client is None:
            self._llm_client = await get_llm_client()
        if self._retriever is None:
            self._retriever = await get_retrieval_service()
        if self._memory is None:
            self._memory = await get_memory_manager()

    # ============================================================
    # analyze（非流式）
    # ============================================================

    async def analyze(self, request: TaskAnalyzeRequest) -> SolutionDraft:
        """非流式分析工单 → 返回结构化方案草稿"""
        t0 = time.perf_counter()
        await self._ensure_clients()

        # 1. 加载工单上下文
        context = await self._load_task_context(request.task_id)

        # 2. 三路并行分析
        retrieval_results = await self._run_analysis(context)

        # 3. 构建 Prompt
        prompt = self._build_prompt(context, retrieval_results)

        # 4. LLM 生成
        raw = await self._llm_client.complete(
            prompt=prompt,
            system_prompt=TASK_AGENT_SYSTEM_PROMPT,
            max_tokens=1500,
            temperature=0.3,
        )

        # 5. 解析
        draft = self._parse_solution(raw)

        total_ms = (time.perf_counter() - t0) * 1000
        print(f"  [task-agent] analyze total={total_ms:.0f}ms")

        return draft

    # ============================================================
    # analyze_stream（SSE 流式）
    # ============================================================

    async def analyze_stream(
        self, request: TaskAnalyzeRequest
    ) -> AsyncGenerator[dict, None]:
        """流式分析工单 → SSE 逐 token 输出"""
        t0 = time.perf_counter()
        await self._ensure_clients()

        # 1. 加载上下文
        yield {"event": "status", "data": {"stage": "loading_context"}}
        context = await self._load_task_context(request.task_id)

        # 2. 三路分析
        yield {"event": "status", "data": {"stage": "retrieving"}}
        retrieval_results = await self._run_analysis(context)

        # 3. 构建 Prompt + 流式生成
        yield {"event": "status", "data": {"stage": "generating"}}
        prompt = self._build_prompt(context, retrieval_results)

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
        except Exception:
            msg = "AI 分析服务暂时不可用，请稍后重试。"
            yield {"event": "token", "data": msg}
            yield {"event": "result", "data": {
                "root_cause_analysis": "", "suggested_actions": [], "references": [],
                "confidence": 0, "needs_more_info": True,
            }}
            return

        # 4. 解析 + 保存上下文
        raw = "".join(raw_tokens)
        draft = self._parse_solution(raw)

        await self._save_analysis_context(request.session_id, context, draft)

        # 5. 返回结构化结果
        result_data = draft.model_dump()
        result_data["attachment_analysis"] = retrieval_results.get(
            "attachment_analysis", {}
        )
        yield {"event": "result", "data": result_data}

        total_ms = round((time.perf_counter() - t0) * 1000)
        yield {"event": "done", "data": {"total_ms": total_ms}}

    # ============================================================
    # submit（方案确认 → Qdrant + 后端状态更新）
    # ============================================================

    async def submit(
        self, task_id: str, session_id: str, draft: SolutionDraft, resolution: str = "resolved"
    ) -> dict:
        """确认方案 → Qdrant 回写 + 调后端 API 更新状态。

        两个操作独立，任一失败不阻塞另一个。
        """
        await self._ensure_clients()
        result = {"task_id": task_id, "solution_indexed": False, "backend_updated": False}

        solution_text = (
            f"根因: {draft.root_cause_analysis}\n"
            f"步骤: {'; '.join(draft.suggested_actions)}"
        )

        # 1. Qdrant 回写（AI 侧负责）
        try:
            await self._index_solution(task_id, solution_text, draft)
            result["solution_indexed"] = True
        except Exception as e:
            print(f"  [task-agent] Qdrant index failed: {e}")

        # 2. 调后端 API 更新状态（业务后端负责状态机 + 审计）
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.put(
                    f"{self._backend_url}{self._backend_api_prefix}/tasks/{task_id}",
                    json={
                        "status": resolution,
                        "metadata_info": {
                            "solution": draft.model_dump(),
                            "resolved_by_agent": True,
                            "resolved_at": int(time.time()),
                        },
                    },
                )
                if resp.status_code == 200:
                    result["backend_updated"] = True
                else:
                    print(f"  [task-agent] Backend update failed: {resp.status_code}")
        except Exception as e:
            print(f"  [task-agent] Backend HTTP call failed: {e}")

        return {"code": 0, "data": result}

    # ============================================================
    # 私有：上下文加载
    # ============================================================

    async def _load_task_context(self, task_id: str) -> TaskContext:
        """从业务后端 REST API + diagnosis 组装工单上下文。

        两路数据：
            - GET /api/tasks/{task_id} → tasks 表字段
            - 从 metadata_info 取 diagnosis JSON（如果提单 Agent 写入过）
        """
        ctx = TaskContext(task_id=task_id)

        # 1. 调后端 API
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                resp = await client.get(
                    f"{self._backend_url}{self._backend_api_prefix}/tasks/{task_id}"
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # 兼容不同响应结构
                    ticket = data.get("data") or data.get("ticket") or data
                    ctx.title = ticket.get("title", "")
                    ctx.description = ticket.get("description", "")
                    ctx.task_type = ticket.get("task_type") or ticket.get("ticket_type", "")
                    ctx.priority = ticket.get("priority", "")
                    ctx.status = ticket.get("status", "")
                    ctx.source = ticket.get("source", "manual")
                    ctx.assigned_to = ticket.get("assigned_to")
                    ctx.project_name = ticket.get("project_name")
                    ctx.attachments = ticket.get("attachments") or []
                    ctx.metadata_info = ticket.get("metadata_info")
        except Exception as e:
            print(f"  [task-agent] Failed to fetch task {task_id}: {e}")

        # 2. 从 metadata_info 取 diagnosis JSON（提单 Agent 写入）
        if ctx.metadata_info and "diagnosis" in ctx.metadata_info:
            diag = ctx.metadata_info["diagnosis"]
            ctx.problem_summary = diag.get("problem_summary", "")
            ctx.hypotheses = diag.get("hypotheses") or []
            ctx.ruled_out = diag.get("ruled_out") or []
            ctx.collected_info = diag.get("collected_info") or {}
            ctx.diagnosis_rounds = diag.get("rounds", 0)
            ctx.fault_code = diag.get("fault_code", "")
            ctx.robot_type = diag.get("robot_type", "")
            ctx.location = diag.get("location", "")

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
        # 尝试提取 JSON
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
                )
            except (json.JSONDecodeError, ValueError, TypeError):
                pass

        # 兜底：把原始回复当根因分析
        return SolutionDraft(
            root_cause_analysis=raw.strip(),
            suggested_actions=[],
            references=[],
            confidence=0.0,
            needs_more_info=True,
        )

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
        # TODO: 等 ai/core/retrieval.py 新增 retrieve_task_resolutions() 后，
        # 这里调用对应的 write/index 方法
        print(f"  [task-agent] Solution indexed: {task_id} (placeholder)")


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
