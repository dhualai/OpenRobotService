"""任务 Agent 核心流水线：上下文加载 → 三路分析 → LLM 生成 → 方案输出

职责边界（硬编码）：
    - 不做诊断（提单 Agent 已完成）
    - 不复诊 hypotheses / ruled_out / collected_info
    - 只检索排查树结论节点 + 历史工单方案 + 附件解析
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Optional

from ai.config import get_ai_config
from ai.core import get_llm_client, get_memory_manager, get_retrieval_service
from ai.core.logging import get_logger
from ai.agents.AiTaskPlatform.schemas import (
    TaskAnalyzeRequest,
    TaskContext,
    SolutionDraft,
    AttachmentAnalysis,
)

# 拆分出的能力模块（pipeline 改薄为编排门面）
from ai.agents.AiTaskPlatform.tracing import Node, TraceBus
from ai.agents.AiTaskPlatform.contexts import (
    load_discussion as _load_discussion_impl,
    add_diagnosis_comment as _add_diagnosis_comment_impl,
    add_diagnosis_comment_short as _add_diagnosis_comment_short_impl,
    load_task_context as _load_task_context_impl,
    is_platform_ticket as _is_platform_ticket_impl,
    build_query as _build_query_impl,
    build_task_ctx as _build_task_ctx_impl,
    build_img_ctx as _build_img_ctx_impl,
)
from ai.agents.AiTaskPlatform.retrieval import rules as _rules
from ai.agents.AiTaskPlatform.attachments.utils import (
    read_attachment_content as _read_attachment_content_impl,
    extract_log_errors as _extract_log_errors_impl,
    extract_log_paths as _extract_log_paths_impl,
)
from ai.agents.AiTaskPlatform.retrieval import format_retrieval_results
from ai.agents.AiTaskPlatform.retrieval import (
    parse_solution_with_status as _parse_solution_with_status_impl,
)
from ai.agents.AiTaskPlatform.prompts import build_user_prompt
from ai.agents.AiTaskPlatform.handlers import SolutionFlow, DiagnoseFlow, DiscussFlow, SummarizeFlow

logger = get_logger("TASK_AGENT")


# ============================================================
# 核心类
# ============================================================

class AiTaskAgent(DiagnoseFlow, DiscussFlow, SummarizeFlow, SolutionFlow):
    """任务 Agent：分析工单 → 生成解决方案草稿

    各功能流程（diagnose/discuss/summarize/analyze/submit）拆分在 handlers/ 下，
    本类通过多继承聚合，并持有共享客户端/trace/私有工具方法。
    """

    # 兼容旧常量引用（仍可 self.NODE_* 访问）
    NODE_OVERHEAD = Node.OVERHEAD
    NODE_LOAD_CONTEXT = Node.LOAD_CONTEXT
    NODE_RETRIEVE = Node.RETRIEVE
    NODE_ATTACHMENT = Node.ATTACHMENT
    NODE_KNOWLEDGE = Node.KNOWLEDGE
    NODE_BUILD_PROMPT = Node.BUILD_PROMPT
    NODE_LLM = Node.LLM
    NODE_PARSE = Node.PARSE
    NODE_DISCUSS = Node.DISCUSS
    NODE_COMMENT = Node.COMMENT
    NODE_SUBMIT = Node.SUBMIT

    def __init__(self):
        self.config = get_ai_config()
        self._llm_client = None
        self._retriever = None
        self._memory = None
        self._trace_bus = TraceBus()
        self._trace = self._trace_bus._trace  # 兼容旧代码直接访问 self._trace

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
        self._trace_bus.add(node, status, **kwargs)

    def _pop_trace(self) -> list:
        """取出全部追踪记录并清空（每次请求独立）"""
        return self._trace_bus.pop()

    # ============================================================
    # analyze（非流式）
    # ============================================================

    def _load_discussion(task_id: str, limit: int = 20) -> str:
        """读取工单讨论评论（含工程师 + U老师/AI 历史分析）。委托 comments.load_discussion。"""
        return _load_discussion_impl(task_id, limit=limit)

    @staticmethod
    def _add_diagnosis_comment_short(task_id: int, content: str) -> Optional[int]:
        """简短回复写入 task_comments。委托 comments.add_diagnosis_comment_short。"""
        return _add_diagnosis_comment_short_impl(task_id, content)

    # ============================================================
    # summarize — 讨论摘要（后端触发 → 扫描所有活跃工单 → 逐条判断生成）
    # ============================================================

    _SUMMARY_MIN_NEW_COMMENTS = 2
    _SUMMARY_ACTIVE_STATUSES = ("new", "pending", "in_progress")

    async def _load_task_context(self, task_id: str) -> TaskContext:
        """从 tasks 表读取工单上下文。委托 contexts.load_task_context。"""
        return _load_task_context_impl(task_id)

    # ============================================================
    # 私有：三路分析
    # ============================================================

    # ── 平台问题关键词（命中 → 启用平台参考文档检索，跳过排查树）──
    # 定义与判断逻辑已拆至 contexts.is_platform_ticket。
    _PLATFORM_ISSUE_KW = []

    @classmethod
    def _is_platform_ticket(cls, context: TaskContext) -> bool:
        """判断工单是否属于服务号平台自身问题。委托 contexts.is_platform_ticket。"""
        return _is_platform_ticket_impl(context)

    async def _run_analysis(self, context: TaskContext) -> dict:
        """条件并行分析：服务号问题查平台文档 → 跳过排查树；
                        AGV/USP 问题查排查树 → 跳过平台文档。"""
        results = {"attachment_analysis": {}, "platform_reference": "", "troubleshooting": ""}

        # 构建检索查询文本（复用 contexts.build_query）
        query_text = _build_query_impl(context)

        is_platform = self._is_platform_ticket(context)

        # 并行执行：历史工单(始终) + 附件(始终) + (排查树 或 平台文档)
        history_task = self._retrieve_task_resolutions(query_text)
        attachment_task = self._parse_attachments(context.attachments)
        if is_platform:
            platform_task = self._retrieve_platform_reference(query_text)
            troubleshooting_task = asyncio.sleep(0)  # no-op，不查排查树
        else:
            troubleshooting_task = self._retrieve_troubleshooting_conclusions(query_text)
            platform_task = asyncio.sleep(0)

        try:
            gathered = await asyncio.gather(
                troubleshooting_task, history_task, attachment_task, platform_task,
            )
            results["troubleshooting"] = gathered[0] if isinstance(gathered[0], str) else "（非调度问题，跳过排查树检索）"
            results["history"] = gathered[1]
            results["attachment_analysis"] = gathered[2]
            results["platform_reference"] = gathered[3] if isinstance(gathered[3], str) else ""
        except Exception as e:
            logger.warning(f"Partial analysis failure: {e}")
            if "troubleshooting" not in results:
                results["troubleshooting"] = "（检索暂不可用）"
            if "history" not in results:
                results["history"] = "（检索暂不可用）"

        return results

    async def _retrieve_troubleshooting_conclusions(self, query: str) -> str:
        """检索排查树，只取结论节点（根因 + 方案）。"""
        try:
            results = await self._retriever.retrieve_troubleshooting(query, top_k=3)
            return format_retrieval_results(results, "troubleshooting")
        except Exception:
            return format_retrieval_results([], "troubleshooting", err=True)

    async def _retrieve_task_resolutions(self, query: str) -> str:
        """检索历史工单方案（Qdrant task_resolutions collection）。"""
        if not hasattr(self._retriever, "retrieve_task_resolutions"):
            return "（task_resolutions collection 尚未建立）"

        try:
            results = await self._retriever.retrieve_task_resolutions(query, top_k=3)
            return format_retrieval_results(results, "task_resolutions")
        except Exception:
            return format_retrieval_results([], "task_resolutions", err=True)

    async def _retrieve_platform_reference(self, query: str) -> str:
        """检索平台参考文档（team domain, sub_domain="product"）。

        覆盖 platform_manual.md（技术架构）和 engineer_guide.md（代码排查）。
        仅在服务号/平台自身问题时启用。
        """
        if not hasattr(self._retriever, "retrieve_platform_reference"):
            return "（平台参考文档检索通道未就绪）"

        try:
            results = await self._retriever.retrieve_platform_reference(query, top_k=3)
            return format_retrieval_results(results, "platform_reference")
        except Exception:
            return format_retrieval_results([], "platform_reference", err=True)

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
        """读取附件文本内容（≤100KB）。委托 attachments.read_attachment_content。"""
        return await _read_attachment_content_impl(att)

    @staticmethod
    def _extract_log_errors(text: str) -> str:
        """从日志文本提取 ERROR/WARN 行摘要。委托 attachments.extract_log_errors。"""
        return _extract_log_errors_impl(text)

    @staticmethod
    def _extract_log_paths(attachments: list) -> tuple[list[str], list[str]]:
        """从附件列表提取日志路径（压缩包先解压）。委托 attachments.extract_log_paths。"""
        return _extract_log_paths_impl(attachments)

    # ============================================================
    # 私有：Prompt 构建 + 解析
    # ============================================================

    def _build_prompt(self, context: TaskContext, retrieval: dict) -> str:
        """组装用户 Prompt 模板。委托 prompt_builder.build_user_prompt。"""
        return build_user_prompt(context, retrieval)

    @staticmethod
    def _parse_solution_with_status(raw: str) -> tuple[SolutionDraft, str]:
        """解析 SolutionDraft JSON，同时返回状态 (ok/json_fail)。委托 solution_io。"""
        return _parse_solution_with_status_impl(raw)

    @staticmethod
    def _build_query(context: TaskContext) -> str:
        """构建检索查询文本。委托 contexts.build_query。"""
        return _build_query_impl(context)

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
        self, task_id: str, solution_text: str, draft: SolutionDraft,
        structured: Optional[dict] = None,
    ) -> None:
        """向量化方案 → 写入 Qdrant task_resolutions collection"""
        try:
            from ai.core.task_adapter import load_task_context_dict
            d = load_task_context_dict(task_id)
            title = d.get("title", f"工单 #{task_id}") or f"工单 #{task_id}"
            fault_code = d.get("fault_code", "")
            robot_type = d.get("robot_type", "")
            problem_summary = d.get("problem_summary", "")

            # P1 结构化根因（如提供，覆盖默认；否则用 safe 默认，兼容旧调用）
            structured = structured or {}

            await self._retriever.index_task_resolution(
                task_id=task_id,
                title=title,
                root_cause=draft.root_cause_analysis,
                solution_steps="；".join(draft.suggested_actions),
                engineer_note=draft.references[0] if draft.references else "",
                fault_code=fault_code,
                robot_type=robot_type,
                problem_summary=problem_summary,
                root_cause_type=structured.get("root_cause_type", "unknown"),
                error_codes=structured.get("error_codes", []),
                severity=structured.get("severity", "unknown"),
                is_common_bug=bool(structured.get("is_common_bug", False)),
                verified=structured.get("verified", "unknown"),
            )
        except Exception as e:
            logger.warning(f"Solution index failed: {e}")

    @staticmethod
    def _add_diagnosis_comment(task_id: int, draft: "SolutionDraft", created_by: str = "U老师") -> bool:
        """将 AI 诊断结果写入 task_comments。委托 comments.add_diagnosis_comment。"""
        return _add_diagnosis_comment_impl(task_id, draft, created_by=created_by)


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
