# -*- coding: utf-8 -*-
"""dar_l1 长会话分窗单测（0913：输出截断致后半 rounds 落默认值 → 滑窗续号）。

mock _classify_once（不依赖 LLM），验证 classify 的窗口切分与 topic 续号拼接。
跑法：python -m pytest ai/tests/test_dar_l1_window.py -q
"""
import asyncio
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "scripts", "dar_l1.py")

_spec = importlib.util.spec_from_file_location("dl1w", _SRC)
dl1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dl1)


def test_no_window_short_conversation(monkeypatch):
    """短会话（≤ 窗口）单次判定，不走分窗。"""
    calls = []

    async def fake_once(rounds, base):
        calls.append((len(rounds), base))
        out = [{"q": True, "t": False, "topic": base} for _ in rounds]
        out.append({"n_topics": 1})
        return out

    monkeypatch.setattr(dl1, "_classify_once", fake_once)
    rounds = [{"q": "hi", "a": ["x"], "at": ""} for _ in range(10)]
    res = asyncio.run(dl1.classify(rounds))
    assert calls == [(10, 0)]
    assert res[-1]["n_topics"] == 1


def test_windowed_topic_base_continues(monkeypatch):
    """超窗分判：每窗 base = 前窗累计话题数；末尾 n_topics = 各窗之和。"""

    async def fake_once(rounds, base):
        # 每窗固定出 2 个话题：前半 base、后半 base+1
        out = []
        half = len(rounds) // 2
        for i in range(len(rounds)):
            out.append({"q": True, "t": False,
                        "topic": base if i < half else base + 1})
        out.append({"n_topics": 2})
        return out

    monkeypatch.setattr(dl1, "_classify_once", fake_once)
    n = dl1.CLASSIFY_WINDOW * 2 + 5  # 三窗：40+40+5
    rounds = [{"q": "q", "a": ["a"], "at": ""} for _ in range(n)]
    res = asyncio.run(dl1.classify(rounds))
    assert len(res) == n + 1
    assert res[-1]["n_topics"] == 6  # 3 窗 × 2 话题
    # 第一窗话题 0/1，第二窗 2/3，第三窗 4/5——跨窗不撞号
    topics = [r["topic"] for r in res[:-1]]
    assert set(topics) == {0, 1, 2, 3, 4, 5}


def test_window_degrade_keeps_base(monkeypatch):
    """某窗降级（n_topics 异常）不炸外壳，续号基准不前进。"""

    async def fake_once(rounds, base):
        if base == 0:
            out = [{"q": True, "t": False, "topic": 0} for _ in rounds]
            out.append({"n_topics": 1})
            return out
        return [{"q": True, "t": False, "topic": base} for _ in rounds] + [
            {"n_topics": 0}]

    monkeypatch.setattr(dl1, "_classify_once", fake_once)
    n = dl1.CLASSIFY_WINDOW + 3
    rounds = [{"q": "q", "a": ["a"], "at": ""} for _ in range(n)]
    res = asyncio.run(dl1.classify(rounds))
    assert res[-1]["n_topics"] == 1
    assert all(r["topic"] == 1 for r in res[:-1][dl1.CLASSIFY_WINDOW:])
