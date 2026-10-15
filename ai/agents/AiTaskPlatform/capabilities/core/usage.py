"""能力调度配额：按「一次请求」计数，超限物理拦下。

为什么要单独一个模块：能力实例是**每次新建**的（`CapabilityRegistry.get()` → `cap_cls()`），
所以计数只能挂在「请求」上，不能挂在实例上——挂在实例上的那版从首个公测版本
`f3cd531f`（2026-08-23）落地起一次都没生效过，见 AiTaskPlatform/issues.md #10。

机制层负责三件事，缺一不可（照诊断侧的「死磕保险」）：
  1) 计数：按本次请求累计每个能力被调度的次数（台账放 runtime_ctx）；
  2) 物理拦下：用满的能力不排进计划、也不执行，而不是靠 prompt 求 LLM 少调；
  3) 明确指令：把「已达上限、不再派发」写进汇总正文，让整理答复的模型给出**有界**结论，
     而不是以为后面还有更深的分析可做。

边界：只管 **LLM 自主调度** 的次数。代码里确定性的兜底调用（如 discuss_flow 按用户
要求补做一次日志分析）不计入——那是产品承诺，不该被配额吃掉。
"""

from __future__ import annotations

from typing import Iterable, Optional

_LEDGER_KEY = "cap_usage"
_NOTES_KEY = "cap_quota_notes"


def ledger(runtime_ctx: Optional[dict]) -> dict:
    """本次请求的用量台账 {能力名: 已调度次数}。ctx 不是 dict 时返回一次性空表。"""
    if not isinstance(runtime_ctx, dict):
        return {}
    book = runtime_ctx.get(_LEDGER_KEY)
    if not isinstance(book, dict):
        book = {}
        runtime_ctx[_LEDGER_KEY] = book
    return book


def limit_of(cap_name: str) -> Optional[int]:
    """能力声明的「每次请求上限」；None = 不限。不实例化能力类。"""
    from ai.agents.AiTaskPlatform.capabilities.core.registry import CapabilityRegistry
    return CapabilityRegistry.limit_of(cap_name)


def used(runtime_ctx: Optional[dict], cap_name: str) -> int:
    return int(ledger(runtime_ctx).get(cap_name) or 0)


def remaining(runtime_ctx: Optional[dict], cap_name: str) -> Optional[int]:
    """还能调度几次；None = 不限。"""
    lim = limit_of(cap_name)
    if lim is None:
        return None
    return max(0, lim - used(runtime_ctx, cap_name))


def claim(runtime_ctx: Optional[dict], cap_name: str) -> bool:
    """占用一次额度。不限的能力直接放行。"""
    lim = limit_of(cap_name)
    if lim is None:
        return True
    book = ledger(runtime_ctx)
    if int(book.get(cap_name) or 0) >= lim:
        return False
    book[cap_name] = int(book.get(cap_name) or 0) + 1
    return True


def drop_exhausted(runtime_ctx: Optional[dict], cap_names: Iterable[str]) -> list[str]:
    """从可选集里摘掉已用满的能力（物理摘除，不是靠 prompt 约束）。"""
    return [n for n in cap_names if remaining(runtime_ctx, n) != 0]


def block(runtime_ctx: Optional[dict], cap_name: str, why: str) -> str:
    """记一条「被拦下」的说明并返回它；同一能力只记一条，避免刷屏。"""
    text = (
        f"⚠️ 能力 {cap_name} 本次请求已达调度上限（{limit_of(cap_name)} 次）：{why}。"
        "请基于以上已有结果给出有界结论，不要假设还有更深的分析。"
    )
    if not isinstance(runtime_ctx, dict):
        return text
    bag = runtime_ctx.setdefault(_NOTES_KEY, [])
    if not any(cap_name in item for item in bag):
        bag.append(text)
    return text


def notes(runtime_ctx: Optional[dict]) -> str:
    """已记录的拦下说明（拼进汇总正文）。"""
    if not isinstance(runtime_ctx, dict):
        return ""
    return "\n".join(runtime_ctx.get(_NOTES_KEY) or [])
