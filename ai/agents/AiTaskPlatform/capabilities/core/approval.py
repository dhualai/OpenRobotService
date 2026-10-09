"""能力执行前的审批策略。

读日志、查知识库、读现场档案默认放行。
会写入长期记忆的能力，必须看到用户原话里的「记住 / 记一下」，
或本轮已经列入 approved_capabilities。否则 before_run 短路，不执行。
"""

from __future__ import annotations

from typing import Any, Optional

from ai.agents.AiTaskPlatform.capabilities.core.base import CapabilityResult

def _approved(kwargs: dict, name: str) -> bool:
    raw = kwargs.get("approved_capabilities") or []
    if isinstance(raw, str):
        raw = [raw]
    return name in {str(item) for item in raw}


def _memory_consented(text: str) -> bool:
    """与讨论入口同一套「记住」识别，避免记下/以后就用被审批拦住。"""
    if not (text or "").strip():
        return False
    from ai.agents.AiTaskPlatform.memory.agent_memory_service import extract_directive_content
    return bool(extract_directive_content(text))


def _user_words(kwargs: dict) -> str:
    """只看用户原话。待写入的 content 不能自己充当同意。"""
    return str(kwargs.get("user_query") or "")


def approval_gate(capability: Any, kwargs: Optional[dict] = None) -> Optional[CapabilityResult]:
    """返回 CapabilityResult 表示拒绝执行；返回 None 表示放行。"""
    if not getattr(capability, "requires_approval", False):
        return None
    data = kwargs if isinstance(kwargs, dict) else {}
    name = str(getattr(capability, "name", "") or "")
    if _approved(data, name):
        return None
    if name == "memory_store" and _memory_consented(_user_words(data)):
        return None
    if name == "memory_store":
        text = "写入长期记忆需要你在这句话里明确说「记住」或「记一下」。这次没有写入。"
    else:
        text = f"能力 {name} 需要事先同意后才会执行。"
    return CapabilityResult(
        text=text,
        meta={"approval": "required", "capability": name},
        ok=False,
        error="需要审批",
    )
