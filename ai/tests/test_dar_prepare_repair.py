# -*- coding: utf-8 -*-
"""dar_prepare 倒挂修复单测（0909：DB 入库倒挂 → 回答挂错轮）。

规则见 dar_prepare._repair_inversions：同秒+≥5 字连续公共片段 / 会话开头孤儿块。
跑法：python -m pytest ai/tests/test_dar_prepare_repair.py -q
"""
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "scripts", "dar_prepare.py")

_spec = importlib.util.spec_from_file_location("dprep", _SRC)
dp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dp)


def _m(role, content, at="2026-01-01T10:00:00"):
    return {"role": role, "content": content, "created_at": at}


def test_same_ts_content_repair():
    """回答行排在提问行之前（同秒 + 回答引用问题原文）→ 归到该提问之后。"""
    lst = [_m("USER", "潜伏车无法上线USP，应该怎么排查", "T1"),
           _m("ASSISTANT", "根据知识库排查手册，先看上轨状态……", "T2"),
           _m("ASSISTANT", "潜伏车无法上线USP的排查步骤：1. 查上轨 2. 查锁区", "T3"),
           _m("USER", "潜伏车无法上线USP，应该怎么排查", "T3")]
    out, moves = dp._repair_inversions(lst)
    assert moves == 1
    assert [x["role"] for x in out] == ["USER", "ASSISTANT", "USER", "ASSISTANT"]
    assert out[3]["content"].startswith("潜伏车无法上线USP的排查步骤")
    assert out[1]["content"].startswith("根据知识库排查手册")  # 前一轮回答不受影响


def test_no_move_when_ts_differs():
    """正常多轮（回答与下一条提问不同秒）不动。"""
    lst = [_m("USER", "问题一怎么处理", "T1"),
           _m("ASSISTANT", "问题一的答案：先检查网络", "T2"),
           _m("USER", "问题二怎么处理", "T3")]
    out, moves = dp._repair_inversions(lst)
    assert moves == 0
    assert out == lst


def test_same_ts_without_overlap_kept():
    """同秒但无内容重叠（问候 → 用户接着提问）不搬——精度护栏。"""
    lst = [_m("USER", "你好", "T1"),
           _m("ASSISTANT", "您好，我是AI客服，请问有什么可以帮您", "T1"),
           _m("USER", "AGV怎么上线部署", "T1")]
    out, moves = dp._repair_inversions(lst)
    assert moves == 0
    assert out == lst


def test_leading_orphans_attach_to_first_user():
    """会话开头的助手消息（原逻辑直接丢弃）整体归给第一条用户消息。"""
    lst = [_m("ASSISTANT", "重置设备就是把车恢复到初始状态", "T0"),
           _m("ASSISTANT", "注意事项：别在运行中重置", "T0"),
           _m("USER", "重置设备有什么作用和注意事项", "T1")]
    out, moves = dp._repair_inversions(lst)
    assert moves == 2
    assert [x["role"] for x in out] == ["USER", "ASSISTANT", "ASSISTANT"]
    assert out[0]["content"].startswith("重置设备有什么作用")


def test_no_user_message_keeps_order():
    """整会话无用户消息（异常数据）原样返回，不吞消息。"""
    lst = [_m("ASSISTANT", "a"), _m("ASSISTANT", "b")]
    out, moves = dp._repair_inversions(lst)
    assert moves == 0
    assert out == lst


def test_user_order_preserved():
    """只搬助手消息：用户消息顺序不变 → 回合编号（astart）稳定。"""
    lst = [_m("ASSISTANT", "跨地图复制的方法：通用复制和科钛车复制", "T1"),
           _m("USER", "跨地图复制功能，可以跨地图组吗", "T1"),
           _m("USER", "上线报错813怎么办", "T2"),
           _m("ASSISTANT", "813是通讯超时，先检查网线", "T2")]
    out, moves = dp._repair_inversions(lst)
    assert moves == 1
    assert [x["content"] for x in out if x["role"] == "USER"] == \
        ["跨地图复制功能，可以跨地图组吗", "上线报错813怎么办"]


def test_share_substr():
    assert dp._share_substr("潜伏车无法上线USP的排查步骤", "潜伏车无法上线USP，应该怎么排查")
    assert not dp._share_substr("您好", "AGV怎么上线部署")   # 短于 n 不判
    assert not dp._share_substr("", "任何内容")
    assert not dp._share_substr("ABCDE", "abcde")           # 大小写敏感
