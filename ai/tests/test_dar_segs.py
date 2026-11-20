# -*- coding: utf-8 -*-
"""dar_segs 段首展开单测（0920：老窗口续聊被旧判定折叠——每周新增统计丢失）。

背景：bounds 只存段首索引、末段恒延伸到会话末尾，老窗口续聊的新回合折叠进
末段、戴着旧判定在漏斗里隐形（0920 实锤：本周 40% 活跃在老窗口，6/11 个续聊
会话新提问完全消失）。effective_starts 统一七处消费方口径。
跑法：python -m pytest ai/tests/test_dar_segs.py -q
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "scripts", "dar_segs.py")

_spec = importlib.util.spec_from_file_location("dsegs", _SRC)
dsegs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dsegs)


def _rounds(ats):
    return [{"q": f"问题{i}", "a": ["回答"], "at": t} for i, t in enumerate(ats)]


ATS = ["2026-09-10 09:00:00", "2026-09-10 09:05:00", "2026-09-10 09:10:00",
       "2026-09-10 09:15:00"]


def test_no_bounds_returns_none():
    """无人工 bounds → None（调用方走 LLM/topic 切分，追加规则不适用）。"""
    rounds = _rounds(ATS)
    assert dsegs.effective_starts(rounds, "1", {}) is None
    assert dsegs.effective_starts(rounds, "1", {"2": [0]}) is None


def test_bounds_expand_basic():
    """bounds 优先展开：0 恒在、越界索引过滤（0915 口径不变）。"""
    rounds = _rounds(ATS)
    assert dsegs.effective_starts(rounds, "1", {"1": [0, 2, 99]}) == [0, 2]
    assert dsegs.effective_starts(rounds, "1", {"1": ["0", "2"]}) == [0, 2]


def test_tail_unjudged_no_append():
    """末段无判定（无人工标签、无预标）→ 不追加：新回合本就在待走查状态。"""
    rounds = _rounds(ATS + ["2026-09-19 10:00:00"])
    got = dsegs.effective_starts(rounds, "1", {"1": [0, 2]}, labels={})
    assert got == [0, 2]


def test_frozen_len_appends():
    """主规则：末段有标签 + frozen_len 记录标注时 4 回合 → 第 5 回合（新增）成新段。"""
    rounds = _rounds(ATS + ["2026-09-19 10:00:00"])
    got = dsegs.effective_starts(
        rounds, "1", {"1": [0, 2]}, labels={"1": {"2": "未直答"}},
        frozen_len={"1": 4})
    assert got == [0, 2, 4]


def test_frozen_len_no_growth_no_append():
    """frozen_len == 当前回合数（无续聊）→ 原样返回。"""
    rounds = _rounds(ATS)
    got = dsegs.effective_starts(
        rounds, "1", {"1": [0, 2]}, labels={"1": {"2": "未直答"}},
        frozen_len={"1": 4})
    assert got == [0, 2]


def test_frozen_len_stale_conservative():
    """frozen_len <= 末段首（bounds 后被人工改过）→ 保守不切。"""
    rounds = _rounds(ATS + ["2026-09-19 10:00:00"])
    got = dsegs.effective_starts(
        rounds, "1", {"1": [0, 2]}, labels={"1": {"2": "未直答"}},
        frozen_len={"1": 2})
    assert got == [0, 2]


def test_gap_fallback_appends():
    """兜底：无 frozen_len，末段有标签，相邻回合间隔 ≥48h → 第一个跳变点切。"""
    rounds = _rounds(ATS + ["2026-09-14 10:00:00", "2026-09-14 10:05:00"])
    got = dsegs.effective_starts(
        rounds, "1", {"1": [0, 2]}, labels={"1": {"2": "未覆盖"}})
    # 09-10 09:15 → 09-14 10:00 = 4 天跳变，新段首=4
    assert got == [0, 2, 4]


def test_gap_fallback_small_gap_no_append():
    """同周内续聊（间隔 <48h）→ 不切（宁可漏切不错切）。"""
    rounds = _rounds(ATS + ["2026-09-11 09:00:00", "2026-09-11 09:05:00"])
    got = dsegs.effective_starts(
        rounds, "1", {"1": [0, 2]}, labels={"1": {"2": "未覆盖"}})
    assert got == [0, 2]


def test_pre_starts_judge_anchor():
    """末段无人工标签但有 L3 预标 → 也算已判定（预标段被续聊同样会折叠）。"""
    rounds = _rounds(ATS + ["2026-09-19 10:00:00"])
    got = dsegs.effective_starts(
        rounds, "1", {"1": [0, 2]}, pre_starts={2}, frozen_len={"1": 4})
    assert got == [0, 2, 4]


def test_pre_starts_empty_is_not_anchor():
    """pre_starts 显式传空集（该会话无预标行）→ 只有预标不算判定。"""
    rounds = _rounds(ATS + ["2026-09-19 10:00:00"])
    got = dsegs.effective_starts(rounds, "1", {"1": [0, 2]}, pre_starts=set())
    assert got == [0, 2]


def test_bad_ts_tolerated():
    """at 缺失/格式坏 → 跳过该跳变判定，不抛异常。"""
    rounds = _rounds(ATS + [None, "garbage"])
    got = dsegs.effective_starts(
        rounds, "1", {"1": [0, 2]}, labels={"1": {"2": "未直答"}})
    assert got == [0, 2]


def test_n_override():
    """cls 与 rounds 长度不一致的调用方：n 显式截断段索引上界。"""
    rounds = _rounds(ATS)
    got = dsegs.effective_starts(
        rounds, "1", {"1": [0, 2, 3]}, labels={"1": {"2": "未直答"}}, n=3)
    assert got == [0, 2]
