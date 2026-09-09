# -*- coding: utf-8 -*-
"""dar_l1 切段规则单测（0909：L1 与下游切法不一致 → 411 vs 418）。

规则见 dar_l1._segments：相邻 topic 变化即断段（与 dar_l3/检索判定/标注工具同源）。
原实现按 topic 值归组，「0,1,0」这类回头话题被并成一段，真实组比下游少 7 段。
跑法：python -m pytest ai/tests/test_dar_l1_segments.py -q
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "scripts", "dar_l1.py")

_spec = importlib.util.spec_from_file_location("dl1", _SRC)
dl1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dl1)


def _cls(topics):
    return [{"q": True, "t": False, "topic": t} for t in topics]


def test_contiguous_topics():
    """连续话题：每段一段，边界落在变化点。"""
    assert dl1._segments(_cls([0, 0, 1, 1, 2])) == [[0, 1], [2, 3], [4]]


def test_noncontiguous_topic_splits():
    """回头话题 0,1,0 必须切成 3 段（原按值归组只出 2 段，正是 411 vs 418 的成因）。"""
    assert dl1._segments(_cls([0, 1, 0])) == [[0], [1], [2]]


def test_missing_topic_key_treated_as_zero():
    """缺 topic 字段的回合按 0 处理，不炸。"""
    cls = [{"q": True, "t": False}, {"q": True, "t": False},
           {"q": True, "t": False, "topic": 1}]
    assert dl1._segments(cls) == [[0, 1], [2]]


def test_empty_cls():
    assert dl1._segments([]) == []


def test_manual_renumber_same_boundaries():
    """人工切分会把 topic 重写成连续编号，按变化断出的边界与人工一致。"""
    assert dl1._segments(_cls([0, 0, 1, 1, 1])) == [[0, 1], [2, 3, 4]]


def test_segments_partition_all_rounds():
    """不丢回合、不重回合：各段长度和 = 回合数，索引恰好覆盖 0..n-1。"""
    for topics in ([0, 1, 0, 2, 2, 0], [0], [0, 0, 0], [0, 1, 2, 3]):
        segs = dl1._segments(_cls(topics))
        flat = [i for s in segs for i in s]
        assert flat == list(range(len(topics)))
