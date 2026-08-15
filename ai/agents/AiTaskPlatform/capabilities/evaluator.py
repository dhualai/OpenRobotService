"""Evaluator — Evaluator-optimizer 自评闭环（改造点 C，G4）

对标 Claude *Building Effective Agents* 的 **Evaluator-optimizer** 模式：
  - LLM 生成初稿后，用一次轻量自评检查质量（偏题/漏错误码/结论与证据矛盾/幻构）
  - 不通过 → 携带 Eval 反馈重写一次（最多 MAX_EVAL_REWRITES 次，防鬼打墙）
  - 仅对"需工具/有证据"的关键输出启用（诊断报告/日志结论/需工具讨论）；纯闲聊不启用（成本护栏）

设计约定（见 TASK_AGENT_TARGET_ARCH.md §5）：
  - MAX_EVAL_REWRITES = 1（重写上限，防循环）
  - Eval 解析失败 → 直接用初稿 + 打 trace 标记 eval_failed（快速失败，不阻塞回复）
  - 产品无关：不绑定工单/产品，用于任何 LLM 输出的质量自评

用法：
    result = Evaluator.evaluate_and_rewrite(
        llm_client=...,
        draft="LLM 初稿",
        evidence="引用的日志行/图片/历史片段",
        context="简短上下文（可选）",
    )
    # 返回 {"final": 最终文本, "rewritten": bool, "eval_notes": [...]}
"""

from __future__ import annotations

import json
import re
from typing import Optional

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

# 防循环护栏
MAX_EVAL_REWRITES = 1   # 重写上限，防鬼打墙


class Evaluator:
    """LLM 输出自评 + 可选重写的工具（纯静态方法，产品无关）。"""

    _EVAL_SYSTEM_PROMPT = (
        "你是严谨的质量评审员。评估一份 AI 生成的回答是否存在问题，只输出 JSON。"
    )

    @staticmethod
    def _build_eval_prompt(draft: str, evidence: str, context: str) -> str:
        return (
            "请评估以下 AI 回答的质量，检查这几类问题：\n"
            "1. 偏题：回答是否偏离了用户问题/任务\n"
            "2. 漏关键信息：是否漏掉了证据里明确指向的错误码/根因\n"
            "3. 结论与证据矛盾：结论是否与引用的证据（日志/图片/历史）不符\n"
            "4. 幻构：是否编造了证据中没有的车型/时间/错误码\n\n"
            f"## 上下文\n{context}\n\n"
            f"## 引用的证据\n{evidence}\n\n"
            f"## 待评估的回答\n{draft}\n\n"
            "输出 JSON（无其他文字）：\n"
            '{"pass": true, "issues": [], "reasoning": "简短理由"}\n'
            '或 {"pass": false, "issues": ["具体问题1", "问题2"], "reasoning": "..."}'
        )

    @staticmethod
    def _parse_eval(raw: str) -> Optional[dict]:
        """解析自评 JSON（健壮解析，剥代码块/散文）。"""
        text = (raw or "").strip()
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
            return None
        if isinstance(data, dict) and "pass" in data:
            return data
        return None

    @staticmethod
    def _build_rewrite_prompt(draft: str, issues: list, evidence: str, context: str) -> str:
        issues_text = "\n".join(f"- {i}" for i in issues) or "（无具体问题）"
        return (
            "请修正以下 AI 回答，解决评审指出的问题。保持准确的表述，不要编造。\n\n"
            f"## 评审问题\n{issues_text}\n\n"
            f"## 上下文\n{context}\n\n"
            f"## 引用的证据\n{evidence}\n\n"
            f"## 原回答（需修正）\n{draft}\n\n"
            "请输出修正后的完整回答（不要输出前缀或解释）。"
        )

    @classmethod
    async def evaluate_and_rewrite(
        cls,
        llm_client,
        draft: str,
        evidence: str = "",
        context: str = "",
    ) -> dict:
        """自评 → （不通过则）重写一次 → 返回最终文本。

        Returns:
            {"final": str, "rewritten": bool, "eval_notes": list, "eval_failed": bool}
        """
        if not draft or not llm_client:
            return {"final": draft, "rewritten": False, "eval_notes": [], "eval_failed": False}

        # 1. 自评
        eval_raw = ""
        try:
            eval_raw = await llm_client.complete(
                prompt=cls._build_eval_prompt(draft, evidence, context),
                system_prompt=cls._EVAL_SYSTEM_PROMPT,
                max_tokens=200,
                temperature=0.0,
            )
        except Exception as e:
            logger.warning(f"[evaluator] 自评调用失败，直接用初稿: {e}")
            return {"final": draft, "rewritten": False, "eval_notes": [], "eval_failed": True}

        eval_data = cls._parse_eval(eval_raw)
        if eval_data is None:
            logger.warning("[evaluator] 自评解析失败，直接用初稿")
            return {"final": draft, "rewritten": False, "eval_notes": [], "eval_failed": True}

        if eval_data.get("pass", False):
            return {"final": draft, "rewritten": False, "eval_notes": [], "eval_failed": False}

        issues = eval_data.get("issues", []) or []
        logger.info(f"[evaluator] 初稿未通过自评，重写（{len(issues)} 个问题）")

        # 2. 重写一次（不超过 MAX_EVAL_REWRITES）
        try:
            rewritten = await llm_client.complete(
                prompt=cls._build_rewrite_prompt(draft, issues, evidence, context),
                system_prompt="你是一个严谨的 AI，根据评审意见修正回答，只输出修正后的完整回答。",
                max_tokens=2000,
                temperature=0.3,
            )
            return {
                "final": (rewritten or draft).strip(),
                "rewritten": True,
                "eval_notes": issues,
                "eval_failed": False,
            }
        except Exception as e:
            logger.warning(f"[evaluator] 重写失败，用初稿: {e}")
            return {"final": draft, "rewritten": False, "eval_notes": issues, "eval_failed": False}
