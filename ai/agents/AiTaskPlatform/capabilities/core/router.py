"""Router — LLM 意图路由（改造点 A，G1）

  - 用轻量 LLM 把用户输入分类成 fixed intent，避免所有输入走同一份重 prompt（单点过载）
  - 为 discuss 新增「纯闲聊」快路径、避免不必要的工具/子 Agent 派生

设计约定（见 TASK_AGENT_TARGET_ARCH.md §3）：
  - temperature=0, max_tokens 很低（意图分类很快）
  - 置信度 < 阈值或解析失败 → 返回 None，调用方（discuss_flow）回退到关键词/默认行为
  - 产品无关：不绑定工单/产品

intent 枚举（与 discuss_flow 对齐）：
  - pure_chat      纯闲聊/常识/平台问答 → 走短 prompt，不派工具
  - log_analysis   有日志附件 & 问日志 → 走日志能力
  - image_analysis 有图片附件 & 问图   → 走图片能力
  - history        查历史相似工单
  - code           查代码
  - general        默认综合（现有行为）
"""

from __future__ import annotations

import json
import re
from typing import Optional

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

# 可用 intent
INTENTS = ["pure_chat", "log_analysis", "image_analysis", "history", "code", "general"]

# 置信度阈值（低于则回退）
CONFIDENCE_THRESHOLD = 0.6


class Router:
    """LLM 意图路由（轻量）。纯静态方法，产品无关。"""

    _SYSTEM_PROMPT = (
        "你是意图分类器。根据用户的问题判断意图，只输出 JSON。"
    )

    @staticmethod
    def _build_prompt(query: str, has_attachments: bool, has_logs: bool) -> str:
        hint = []
        if has_attachments:
            hint.append("- 有工单附件")
        if has_logs:
            hint.append("- 附件含日志文件")
        hint_str = ("\n".join(hint)) or "- 无附件"
        return (
            "判断下面用户问题的意图（从候选里选一个）：\n\n"
            f"## 工单上下文\n{hint_str}\n\n"
            f"## 用户问题\n{query}\n\n"
            "# 意图候选\n"
            "- pure_chat     : 纯闲聊/常识/问AI能做什么/礼貌寒暄\n"
            "- log_analysis  : 明确要分析日志、看错误/异常/时序\n"
            "- image_analysis: 明确要看图片/截图/照片\n"
            "- history       : 查历史相似工单/参考以前案例\n"
            "- code          : 要看代码/源码/实现逻辑\n"
            "- general       : 综合问题，需要知识库/方案建议\n\n"
            "# 另一个字段（顺带判，不要为它多花心思）\n"
            "- challenge：用户是否在**质疑/推翻此前的结论**"
            "（「你上次分析错了」「不对吧」「重新看一遍」「之前那个判断有问题」）。\n"
            "  仅在明确质疑时 true；追问细节、补充信息、换个问法都算 false。\n\n"
            "输出 JSON（无其他文字）：\n"
            '{"intent": "<one of>", "confidence": 0.0~1.0, "challenge": true|false}'
        )

    @staticmethod
    def _parse(raw: str) -> Optional[dict]:
        """解析意图 JSON，返回 {"intent", "confidence"} 或 None。"""
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
        if isinstance(data, dict) and data.get("intent") in INTENTS:
            return {
                "intent": data["intent"],
                "confidence": float(data.get("confidence", 0)),
                # challenge：用户是否在质疑此前的结论。分类器没给就按「不是」处理（保守）。
                "challenge": bool(data.get("challenge", False)),
            }
        return None

    @classmethod
    async def classify_full(
        cls,
        llm_client,
        query: str,
        has_attachments: bool = False,
        has_logs: bool = False,
    ) -> Optional[dict]:
        """完整分类结果：{"intent","confidence","challenge"}。

        调用失败 / 解析失败 / 置信度低于阈值 → **返回 None**，调用方须回退到
        关键词或默认行为（这是本模块的既定约定，不要在这里编一个默认值糊过去）。
        """
        if not query or not llm_client:
            return None
        try:
            raw = await llm_client.complete(
                prompt=cls._build_prompt(query, has_attachments, has_logs),
                system_prompt=cls._SYSTEM_PROMPT,
                max_tokens=60,
                temperature=0.0,
            )
        except Exception as e:
            logger.warning(f"[router] 意图分类失败: {e}")
            return None

        parsed = cls._parse(raw)
        if parsed is None:
            logger.warning("[router] 意图解析失败")
            return None
        if parsed["confidence"] < CONFIDENCE_THRESHOLD:
            logger.info(f"[router] 置信度低({parsed['confidence']})，交由调用方回退")
            return None
        return parsed

    @classmethod
    async def classify(
        cls,
        llm_client,
        query: str,
        has_attachments: bool = False,
        has_logs: bool = False,
        fallback: str = "general",
    ) -> str:
        """只要 intent 的便捷入口；拿不到就返回 fallback。"""
        full = await cls.classify_full(llm_client, query, has_attachments, has_logs)
        return full["intent"] if full else fallback
