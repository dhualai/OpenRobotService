"""状态刷新 Worker：水位线切片与失败安全。

不连库、不调 LLM：monkeypatch 掉三个 DB 函数，用假 LLM。
"""
import asyncio

from ai.agents.AiTaskPlatform.services import agent_state_worker as w


class _Cfg:
    agent_state_raw_window = 3


class _FakeLLM:
    def __init__(self, reply='{"goal": "新目标"}'):
        self.reply = reply
        self.calls = 0

    async def complete(self, prompt, system_prompt=None, max_tokens=2000,
                       temperature=0.1, thinking=False):
        self.calls += 1
        return self.reply


class _BoomLLM:
    async def complete(self, *a, **k):
        raise AssertionError("这一步不该调 LLM")


def _rows(n, start=1):
    return [
        {"id": i, "author": "张三" if i % 2 else "李四", "content": f"第{i}条"}
        for i in range(start, start + n)
    ]


def _patch(monkeypatch, *, state, rows, saved):
    monkeypatch.setattr(w, "get_ai_config", lambda: _Cfg())
    monkeypatch.setattr(w, "load_state", lambda tid: state)
    monkeypatch.setattr(w, "load_comment_rows_after", lambda tid, after_id=0: rows)

    def _save(tid, st, wm, rounds):
        saved.append({"task_id": tid, "state": st, "watermark": wm, "rounds": rounds})
        return True

    monkeypatch.setattr(w, "save_state", _save)


def test_keeps_recent_window_out_of_state(monkeypatch):
    """最近 raw_window 条留在原文里，不折叠进状态 —— 这是分层的关键。"""
    saved = []
    _patch(monkeypatch, state={"state": None, "covered_comment_id": 0, "rounds": 0},
           rows=_rows(10), saved=saved)
    res = asyncio.run(w.refresh_ticket_state(1, _FakeLLM()))

    assert res["ok"] is True
    # 10 条 - 窗口 3 条 = 折叠 7 条
    assert res["folded"] == 7
    # 水位线停在第 7 条（第 8~10 条留给原文）
    assert saved[0]["watermark"] == 7
    assert saved[0]["rounds"] == 1


def test_incremental_only_folds_beyond_watermark_and_window(monkeypatch):
    saved = []
    # 水位线已到 7，新增 8~12 共 5 条；窗口 3 → 只折叠 8、9
    _patch(monkeypatch, state={"state": {"goal": "旧"}, "covered_comment_id": 7, "rounds": 1},
           rows=_rows(5, start=8), saved=saved)
    res = asyncio.run(w.refresh_ticket_state(1, _FakeLLM()))

    assert res["ok"] is True
    assert res["folded"] == 2
    assert saved[0]["watermark"] == 9
    assert saved[0]["rounds"] == 2


def test_new_comments_within_window_skip_without_llm(monkeypatch):
    """新评论没超出原文窗口 → 不调 LLM（避免每轮白花钱）。"""
    saved = []
    _patch(monkeypatch, state={"state": None, "covered_comment_id": 0, "rounds": 0},
           rows=_rows(2), saved=saved)   # 2 <= 窗口 3
    res = asyncio.run(w.refresh_ticket_state(1, _BoomLLM()))

    assert res["skipped"] is True
    assert saved == []


def test_no_new_comments_skips(monkeypatch):
    saved = []
    _patch(monkeypatch, state={"state": {"goal": "旧的"}, "covered_comment_id": 9, "rounds": 1},
           rows=[], saved=saved)
    res = asyncio.run(w.refresh_ticket_state(1, _BoomLLM()))

    assert res["skipped"] is True
    assert saved == []


def test_llm_failure_does_not_advance_watermark(monkeypatch):
    """调用失败 → 不保存、水位线不动，下次 @U老师 自然重试。"""
    saved = []
    _patch(monkeypatch, state={"state": None, "covered_comment_id": 0, "rounds": 0},
           rows=_rows(10), saved=saved)
    llm = _FakeLLM(reply="完全不是 JSON")

    res = asyncio.run(w.refresh_ticket_state(1, llm))

    assert res["skipped"] is True
    assert saved == []


def test_idempotent_when_already_covered(monkeypatch):
    """同一工单被重复入队：第二次跑时无可折叠 → 自动跳过（队列方案的幂等性）。"""
    saved = []
    _patch(monkeypatch, state={"state": {"goal": "旧的"}, "covered_comment_id": 100, "rounds": 5},
           rows=[], saved=saved)
    res = asyncio.run(w.refresh_ticket_state(1, _BoomLLM()))

    assert res["skipped"] is True
    assert saved == []
