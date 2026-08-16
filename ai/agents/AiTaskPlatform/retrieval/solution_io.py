"""解决方案解析/序列化工具（从 pipeline.py 拆分纯逻辑，不依赖 self）

职责：
  - parse_solution_with_status: 从 LLM 原始输出解析 SolutionDraft JSON
"""

import json
import re

from ai.agents.AiTaskPlatform.schemas import SolutionDraft


def parse_solution_with_status(raw: str) -> tuple:
    """解析 SolutionDraft JSON，同时返回状态 (ok/json_fail)。"""
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
            ), "ok"
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # 兜底：从 Markdown 提取 sections
    root_cause = raw
    actions: list = []
    refs: list = []
    conf = 0.0
    m = re.search(r"根因分析[：:]\s*(.+?)(?=###\s|建议步骤|证据|$)", raw, re.DOTALL)
    if m:
        root_cause = m.group(1).strip()[:800]
    for m in re.finditer(r"\d+\.\s*\*?\*?\s*(.+?)(?=\n\d+\.|\n\n|###|$)", raw):
        a = m.group(1).strip()
        if a and len(a) > 5:
            actions.append(a[:200])
    m = re.search(r"置信度[：:]\s*.?(\d+\.?\d*)", raw)
    if m:
        try:
            conf = float(m.group(1))
        except ValueError:
            pass
    return SolutionDraft(
        root_cause_analysis=root_cause.strip() or raw.strip(),
        suggested_actions=actions or [],
        references=refs or [],
        confidence=conf if not actions else max(conf, 0.5),
        needs_more_info=conf < 0.5,
    ), "json_fail"
