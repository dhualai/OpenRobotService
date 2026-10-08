"""USP 拉日志时间锚点：结构化字段 → 正文正则 → LLM 提取。

不用工单 created_at 顶替；材料里没有明确故障时刻时返回空，由上层决定是否回退 now。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

from ai.core.logging import get_logger

logger = get_logger("TASK_AGENT")

_TIME_KEYS = (
    "occurrence_time", "发生时间", "时间",
    "occurred_at", "occurred", "time", "event_time",
)

_SYSTEM = (
    "你是故障时间提取器。只从给定材料里找出故障/异常发生的具体时刻，"
    "不要用工单创建时间、当前时间或推测时间顶替。没有明确时刻就返回空。"
)


def _ci_get(ci: Dict[str, Any], key: str) -> str:
    val = ci.get(key)
    if val is None:
        return ""
    if isinstance(val, str):
        return val.strip()
    return str(val).strip()


def from_structured(
    collected_info: Optional[Dict[str, Any]] = None,
    occurrence_time: str = "",
) -> Tuple[str, str]:
    """结构化字段优先。返回 (raw, source)；没有则 ("", "")。"""
    ci = collected_info or {}
    for key in _TIME_KEYS:
        val = _ci_get(ci, key)
        if val:
            return val, f"collected_info.{key}"
    meta = (occurrence_time or "").strip()
    if meta:
        return meta, "task.occurrence_time"
    return "", ""


def from_text_regex(*texts: str) -> Tuple[str, str]:
    """正文/提问里直接扫 ``YYYY-MM-DD HH:MM`` 一类明确时间。"""
    try:
        from ai.agents.AiTaskPlatform.log_analyzer.log_window import (
            has_time_in_query,
            parse_occurred_at,
        )
    except Exception:
        return "", ""

    for i, raw in enumerate(texts):
        text = (raw or "").strip()
        if not text or not has_time_in_query(text):
            continue
        # has_time 命中后，用 parse 把第一处时间抽出
        m = re.search(
            r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?)",
            text,
        )
        candidate = m.group(1) if m else text
        if parse_occurred_at(candidate) is not None:
            return candidate.replace("/", "-").replace("T", " "), f"regex.text[{i}]"
    return "", ""


def _parse_llm_json(raw: str) -> Optional[dict]:
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
    return data if isinstance(data, dict) else None


async def from_llm(
    llm_client,
    *,
    title: str = "",
    description: str = "",
    problem_summary: str = "",
    collected_info: Optional[Dict[str, Any]] = None,
    query: str = "",
    discussion: str = "",
) -> Tuple[str, str]:
    """让大模型从工单材料里填发生时间。返回 (raw, source)。"""
    if not llm_client:
        return "", ""
    ci = collected_info or {}
    ci_text = ""
    try:
        ci_text = json.dumps(ci, ensure_ascii=False)[:800]
    except Exception:
        ci_text = str(ci)[:800]

    prompt = (
        "从下列工单材料中提取故障/异常发生时间（精确到分钟更好）。\n"
        "规则：\n"
        "1. 只提取材料里已经写明的时间，不要编造。\n"
        "2. 不要用工单创建时间、评论发送时间或「现在」顶替。\n"
        "3. 材料里没有明确发生时刻 → occurrence_time 填空字符串。\n"
        "4. 有多个时间时，优先「发生时间/故障时间/报障时间」，其次描述里最贴近故障的时刻。\n\n"
        f"## 标题\n{(title or '')[:200]}\n\n"
        f"## 描述\n{(description or '')[:1200]}\n\n"
        f"## 问题摘要\n{(problem_summary or '')[:400]}\n\n"
        f"## 已收集信息\n{ci_text or '（无）'}\n\n"
        f"## 用户本轮提问\n{(query or '')[:400]}\n\n"
        f"## 最近讨论\n{(discussion or '')[:600]}\n\n"
        "只输出 JSON：\n"
        '{"occurrence_time": "YYYY-MM-DD HH:MM 或空", "confidence": 0.0}'
    )
    try:
        raw = await llm_client.complete(
            prompt=prompt,
            system_prompt=_SYSTEM,
            max_tokens=60,
            temperature=0.0,
        )
    except Exception as e:
        logger.warning(f"[occurrence] LLM 提取失败: {e}")
        return "", ""

    parsed = _parse_llm_json(raw)
    if not parsed:
        logger.warning(f"[occurrence] LLM 返回无法解析: {(raw or '')[:120]}")
        return "", ""
    occ = str(parsed.get("occurrence_time") or "").strip()
    conf = float(parsed.get("confidence") or 0)
    if not occ:
        return "", ""
    if conf < 0.5:
        logger.info(f"[occurrence] LLM 置信度低 conf={conf} raw={occ!r}，丢弃")
        return "", ""
    # 再过一遍可解析校验
    try:
        from ai.agents.AiTaskPlatform.log_analyzer.log_window import parse_occurred_at
        from ai.agents.AiTaskPlatform.server_pull.timeutil import to_export_time_str
        if parse_occurred_at(occ) is None and len(
            re.sub(r"\D+", "", occ)
        ) < 8:
            logger.info(f"[occurrence] LLM 时间不可解析: {occ!r}")
            return "", ""
        # 能转成 export 格式即可
        _ = to_export_time_str(occ)
    except Exception:
        pass
    return occ, "llm.extract"


async def resolve_occurrence_time(
    llm_client=None,
    *,
    collected_info: Optional[Dict[str, Any]] = None,
    occurrence_time: str = "",
    title: str = "",
    description: str = "",
    problem_summary: str = "",
    query: str = "",
    discussion: str = "",
) -> Tuple[str, str]:
    """解析故障发生时间。返回 ``(raw, source)``；都没有则 ``("", "")``。"""
    raw, src = from_structured(collected_info, occurrence_time)
    if raw:
        return raw, src

    raw, src = from_text_regex(query, description, problem_summary, title, discussion)
    if raw:
        return raw, src

    raw, src = await from_llm(
        llm_client,
        title=title,
        description=description,
        problem_summary=problem_summary,
        collected_info=collected_info,
        query=query,
        discussion=discussion,
    )
    return raw, src
