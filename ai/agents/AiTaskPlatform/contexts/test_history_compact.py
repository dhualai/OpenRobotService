"""未到窗口用原文；到了窗口才压缩更早的材料，最近轮次保持原文。"""
import asyncio

from ai.agents.AiTaskPlatform.contexts.history_compact import (
    estimate_tokens,
    fit_ticket_history,
    select_kept,
)


def test_under_window_keeps_original_description_and_comments():
    desc = "车停在路径规划中，已经等了十分钟。"
    lines = ["[张三] 调度没有下发路径", "[U老师] 先看规划是否一直重试"]
    kept, older, recent = select_kept(desc, lines, fixed_tokens=100, limit=10_000)
    assert older == []
    assert kept == desc
    assert recent == lines


def test_over_window_folds_oldest_and_keeps_latest_raw():
    desc = "描述" * 50
    lines = [f"[u{i}] " + ("第%d条" % i) * 40 for i in range(6)]
    limit = estimate_tokens(desc) + estimate_tokens(lines[-1]) + 30
    kept, older, recent = select_kept(desc, lines, fixed_tokens=10, limit=limit, summary_budget=8)
    assert older
    assert recent[-1] == lines[-1]
    assert lines[0] in "\n".join(older)


def test_fit_does_not_call_model_under_window():
    class Boom:
        async def complete(self, **kwargs):
            raise AssertionError("未到窗口不应压缩")

    desc, disc = asyncio.run(fit_ticket_history(
        description="车不动",
        comment_lines=["[张三] 还在规划"],
        fixed_text="用户问为什么不动",
        llm_client=Boom(),
        limit=10_000,
    ))
    assert desc == "车不动"
    assert "[张三] 还在规划" in disc
    assert "压缩摘要" not in disc


def test_fit_summarizes_only_the_overflow():
    calls = []

    class Fake:
        async def complete(self, prompt, system_prompt=None, max_tokens=2000, temperature=0.1, thinking=False):
            calls.append(prompt)
            return "1. 目标\n车停在路径规划中\n6. 关键标识\n车号 A1"

    lines = ["[开单] " + "早" * 80, "[张三] " + "中" * 80, "[U老师] 最近这条要原样留下"]
    desc, disc = asyncio.run(fit_ticket_history(
        description="现象",
        comment_lines=lines,
        fixed_text="问",
        llm_client=Fake(),
        limit=estimate_tokens(lines[-1]) + 40,
    ))
    assert calls
    assert "早" in calls[0]
    assert "最近这条要原样留下" not in calls[0]
    assert "1. 目标" in disc
    assert "[U老师] 最近这条要原样留下" in disc
    assert desc
