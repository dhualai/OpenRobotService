"""AI 数据分析平台 · 数据分析引擎

负责数据预处理、调用 LLM 完成分析、解析结构化结果。
"""

from __future__ import annotations

import io
import json
import logging
import re
from typing import Any

import pandas as pd
from tabulate import tabulate

from .llm_client import LLMClient
from .prompts import build_system_prompt, build_user_prompt
from .schemas import (
    AnalysisInsight,
    AnalysisResult,
    AnalysisType,
    DataSource,
)

logger = logging.getLogger(__name__)


class DataAnalyzer:
    """数据分析引擎。

    将原始数据预处理为 LLM 友好的文本格式，调用大模型完成分析，
    并将回复解析为结构化的 :class:`AnalysisResult`。

    用法::

        analyzer = DataAnalyzer(llm_client)
        result = await analyzer.analyze(
            data=raw_data,
            analysis_type=AnalysisType.FAULT_ANALYSIS,
        )
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    # ── 数据预处理 ──────────────────────────────────────────

    @staticmethod
    def preprocess_data(data: str, data_source: DataSource) -> str:
        """将原始数据预处理为 LLM 易读的文本格式。

        - JSON: 尝试格式化美化
        - CSV: 转为 Markdown 表格
        - 其他: 原样返回
        """
        if not data or not data.strip():
            raise ValueError("待分析数据为空")

        if data_source == DataSource.JSON:
            try:
                parsed = json.loads(data)
                return json.dumps(parsed, ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                logger.warning("数据标记为 JSON 但解析失败，按原始文本处理")
                return data

        if data_source == DataSource.CSV:
            try:
                df = pd.read_csv(io.StringIO(data))
                return tabulate(df, headers="keys", tablefmt="pipe", showindex=False)
            except Exception:
                logger.warning("CSV 解析失败，按原始文本处理")
                return data

        return data

    # ── 核心分析 ────────────────────────────────────────────

    async def analyze(
        self,
        data: str,
        data_source: DataSource = DataSource.JSON,
        analysis_type: AnalysisType = AnalysisType.GENERAL,
        question: str | None = None,
        context: str | None = None,
    ) -> AnalysisResult:
        """执行数据分析并返回结构化结果。

        Args:
            data: 原始数据字符串。
            data_source: 数据格式。
            analysis_type: 分析类型。
            question: 用户的具体分析问题（可选）。
            context: 补充上下文（可选）。

        Returns:
            结构化分析结果。
        """
        # 1. 数据预处理
        processed_data = self.preprocess_data(data, data_source)

        # 2. 构建 prompt
        system_prompt = build_system_prompt(analysis_type)
        user_prompt = build_user_prompt(
            data=processed_data,
            data_source=data_source,
            question=question,
            context=context,
        )

        # 3. 调用 LLM
        logger.info(
            "开始数据分析 type=%s data_source=%s data_len=%d",
            analysis_type.value,
            data_source.value,
            len(processed_data),
        )
        raw_response, usage = await self._llm.chat(system_prompt, user_prompt)

        # 4. 解析结果
        result = self._parse_result(raw_response, analysis_type)
        result.raw_response = raw_response
        result.model = self._llm.model_name
        result.usage = usage

        logger.info("分析完成 insights=%d", len(result.insights))
        return result

    async def analyze_stream(
        self,
        data: str,
        data_source: DataSource = DataSource.JSON,
        analysis_type: AnalysisType = AnalysisType.GENERAL,
        question: str | None = None,
        context: str | None = None,
    ):
        """流式分析，逐 chunk 返回文本。

        Args:
            同 :meth:`analyze`。

        Yields:
            模型输出的文本片段。
        """
        processed_data = self.preprocess_data(data, data_source)

        system_prompt = build_system_prompt(analysis_type)
        user_prompt = build_user_prompt(
            data=processed_data,
            data_source=data_source,
            question=question,
            context=context,
        )

        async for chunk in self._llm.chat_stream(system_prompt, user_prompt):
            yield chunk

    # ── 结果解析 ────────────────────────────────────────────

    @staticmethod
    def _parse_result(
        raw_response: str, analysis_type: AnalysisType
    ) -> AnalysisResult:
        """将 LLM 的 Markdown 回复解析为结构化结果。

        解析策略：
        - 摘要：提取第一个 ``**摘要**`` 后的文本
        - 洞察：提取 ``###`` 标题下的内容
        - 建议：提取 ``行动建议`` 部分的有序列表
        """
        summary = DataAnalyzer._extract_summary(raw_response)
        insights = DataAnalyzer._extract_insights(raw_response)
        recommendations = DataAnalyzer._extract_recommendations(raw_response)

        return AnalysisResult(
            analysis_type=analysis_type,
            summary=summary,
            insights=insights,
            recommendations=recommendations,
        )

    @staticmethod
    def _extract_summary(text: str) -> str:
        """提取摘要（第一个段落或 **摘要** 标记后的内容）。"""
        # 尝试匹配 **摘要** / ## 摘要
        patterns = [
            r"\*\*摘要\*\*[：:\s]*(.+?)(?:\n\n|\n###|\n##|$)",
            r"##\s*摘要[：:\s]*(.+?)(?:\n\n|\n###|\n##|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                return match.group(1).strip()

        # 兜底：取第一段非标题文本
        lines = text.strip().split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith("**"):
                return stripped
        return text[:200]

    @staticmethod
    def _extract_insights(text: str) -> list[AnalysisInsight]:
        """提取 ### 标题下的洞察。"""
        insights: list[AnalysisInsight] = []
        # 匹配 ### 标题及其后续内容
        pattern = r"^###\s+(.+?)\n((?:(?!###|##).+?\n)*)"
        for match in re.finditer(pattern, text, re.MULTILINE):
            title = match.group(1).strip()
            content = match.group(2).strip()
            if not content:
                continue

            # 判断严重程度
            severity = "info"
            if any(kw in title for kw in ["严重", "危险", "critical", "告警"]):
                severity = "critical"
            elif any(kw in title for kw in ["警告", "异常", "warning", "风险"]):
                severity = "warning"

            insights.append(
                AnalysisInsight(
                    category=title,
                    content=content,
                    severity=severity,
                )
            )
        return insights

    @staticmethod
    def _extract_recommendations(text: str) -> list[str]:
        """提取行动建议部分的有序列表项。"""
        recommendations: list[str] = []

        # 找到"行动建议"部分
        pattern = r"(?:行动建议|建议|##\s*建议|##\s*行动建议)[：:\s]*\n((?:(?!\n##).+?\n)*)"
        match = re.search(pattern, text, re.MULTILINE)
        if not match:
            return recommendations

        section = match.group(1)
        # 提取有序列表项
        for line in section.strip().split("\n"):
            stripped = line.strip()
            # 匹配 "1. xxx" 或 "- xxx" 或 "* xxx"
            item_match = re.match(r"^(?:\d+\.|[-*])\s+(.+)", stripped)
            if item_match:
                recommendations.append(item_match.group(1).strip())

        return recommendations
