"""任务 Agent 分析引擎：三路并行分析编排

三路分析：
    1. 排查树结论检索 — 只取结论节点的根因 + 方案
    2. 历史工单方案检索 — Qdrant task_resolutions 语义检索
    3. 附件解析 — 日志/回放关键信息提取

复用 ai.core.retrieval 的已有方法，不重写检索逻辑。
"""

import json
from typing import Optional

from ai.config import get_ai_config
from ai.core import get_retrieval_service
from ai.agents.AiTaskPlatform.schemas import TaskContext, AttachmentAnalysis
from ai.agents.AiTaskPlatform.attachment_parser import parse_attachments


class AnalysisResults:
    """三路分析结果容器"""

    def __init__(self):
        self.troubleshooting: str = "（排查树检索未执行）"
        self.history: str = "（历史方案检索未执行）"
        self.attachment: AttachmentAnalysis = AttachmentAnalysis()

    def to_dict(self) -> dict:
        return {
            "troubleshooting": self.troubleshooting,
            "history": self.history,
            "attachment_analysis": self.attachment.model_dump() if self.attachment else {},
        }


class TaskAnalyzer:
    """任务分析引擎：编排三路并行检索 + 附件解析"""

    def __init__(self):
        self.config = get_ai_config()
        self._retriever = None

    async def _get_retriever(self):
        if self._retriever is None:
            self._retriever = await get_retrieval_service()
        return self._retriever

    async def analyze(self, context: TaskContext) -> AnalysisResults:
        """三路并行分析。

        Args:
            context: 工单完整上下文（diagnosis + tasks 表字段）

        Returns:
            AnalysisResults: 三路结果容器
        """
        import asyncio

        results = AnalysisResults()
        retriever = await self._get_retriever()

        # 构建检索查询文本
        query_text = self._build_query(context)

        # 三路并行
        try:
            t_trouble, t_history, t_attachment = await asyncio.gather(
                self._retrieve_troubleshooting(retriever, query_text),
                self._retrieve_task_resolutions(retriever, query_text),
                parse_attachments(context.attachments),
                return_exceptions=True,
            )

            if not isinstance(t_trouble, Exception):
                results.troubleshooting = t_trouble
            if not isinstance(t_history, Exception):
                results.history = t_history
            if not isinstance(t_attachment, Exception):
                results.attachment = t_attachment

        except Exception as e:
            print(f"  [task-analyzer] Partial failure: {e}")

        return results

    # ── 查询构建 ──────────────────────────────────────────────

    @staticmethod
    def _build_query(context: TaskContext) -> str:
        """构建检索查询文本（结合 diagnosis + 工单信息）。"""
        parts = []

        # 问题摘要（第一优先级）
        if context.problem_summary:
            parts.append(context.problem_summary)
        elif context.description:
            parts.append(context.description)

        # 推测原因 → 帮助精确匹配排查树
        if context.hypotheses:
            parts.append(" ".join(context.hypotheses))

        # 故障码 → 精确匹配
        if context.fault_code:
            parts.append(context.fault_code)

        # 车型 → 缩小范围
        if context.robot_type:
            parts.append(context.robot_type)

        query = " ".join(parts) if parts else context.description
        return query

    # ── 排查树结论检索 ───────────────────────────────────────

    async def _retrieve_troubleshooting(self, retriever, query: str) -> str:
        """检索排查树，只取结论节点（根因 + 方案）。

        与提单 Agent 不同：提单 Agent 用排查树做分流+步骤引导，
        任务 Agent 只需要结论节点（已经走到结论或接近结论的路径）。
        """
        try:
            results = await retriever.retrieve_troubleshooting(query, top_k=3)
            if not results:
                return "（无匹配的排查树结论）"

            lines = []
            for i, r in enumerate(results, 1):
                title = r.title or ""
                content = r.content or ""
                if content.strip():
                    # 排查树内容包含完整路径 → 只提取结论部分
                    conclusion = self._extract_conclusion(content)
                    lines.append(
                        f"排查树 {i}：{title}\n"
                        f"{conclusion}\n"
                        f"---"
                    )

            return "\n".join(lines) if lines else "（排查树匹配但无有效结论）"
        except Exception as e:
            print(f"  [task-analyzer] Troubleshooting retrieval failed: {e}")
            return "（排查树检索失败）"

    @staticmethod
    def _extract_conclusion(tree_text: str) -> str:
        """从排查树文本中提取结论节点。

        排查树格式示例：
            第1步：确认XX → 用户说有 → 【结论】原因：XX。方案：XX
            第2步：检查YY → ...

        提取所有【结论】标记的节点，保留根因+方案。
        """
        import re
        conclusions = re.findall(
            r"【结论】[^。\n]*(?:。|$)",
            tree_text,
        )
        if conclusions:
            return "；\n".join(conclusions)

        # 没有明确的结论标记 → 返回最后的部分（通常包含结论信息）
        lines = tree_text.strip().split("\n")
        return "\n".join(lines[-5:]) if len(lines) > 5 else tree_text

    # ── 历史工单方案检索 ─────────────────────────────────────

    async def _retrieve_task_resolutions(self, retriever, query: str) -> str:
        """检索历史工单方案（Qdrant task_resolutions collection）。

        查询文本 = problem_summary + hypotheses + fault_code + robot_type。
        """
        # 检查检索服务是否支持 task_resolutions
        if not hasattr(retriever, "retrieve_task_resolutions"):
            return "（task_resolutions collection 尚未建立，跳过）"

        try:
            results = await retriever.retrieve_task_resolutions(query, top_k=3)
            if not results:
                return "（无相似的历史工单方案）"

            lines = []
            for i, r in enumerate(results, 1):
                title = r.title or f"工单 #{r.id}"
                content = r.content or ""
                score = getattr(r, "score", 0)
                if content.strip():
                    lines.append(
                        f"历史工单 {i}：{title}"
                        + (f"（相似度 {score:.2f}）" if score else "")
                        + f"\n{content}\n---"
                    )

            return "\n".join(lines) if lines else "（无相似的历史工单方案）"
        except Exception as e:
            print(f"  [task-analyzer] Task resolutions retrieval failed: {e}")
            return "（历史方案检索失败）"
