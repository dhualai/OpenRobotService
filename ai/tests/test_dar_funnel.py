# -*- coding: utf-8 -*-
"""开发者模式「AI 直答率 · 每周漏斗」的数据计算（ORS-716）。

覆盖：六层分层计数、周分桶边界（周一为起点）、本周 partial 标记、
直答率分母只算已判定三层（与工作台同口径）。
"""
import datetime as dt
import sys
import types

import pytest

from ai.scripts import dar_weekly as dw  # noqa: E402


def _row(layer, eff="", at="2026-11-17 10:00:00"):
    return {"layer": layer, "eff": eff, "at": at}


def test_funnel_block_counts_and_derived():
    rows = (
        [_row("tester")] * 3
        + [_row("chitchat")] * 2
        + [_row("suggested", "直答正确")] * 4
        + [_row("ticket")] * 5
        + [_row("undetermined")] * 1
        + [_row("answered", "直答正确")] * 6
        + [_row("unanswered", "未直答")] * 2
        + [_row("uncovered", "未覆盖")] * 3
    )
    f = dw._funnel_block(rows)
    assert f["total"] == 26
    assert f["real"] == 26 - 3 - 2 - 4          # 扣测试/闲聊/推荐池混合
    assert f["judged"] == 6 + 2 + 3             # 三层
    assert f["correct"] == 6                    # 推荐池里的直答正确不计入分母
    assert f["wrong"] == 2 and f["uncovered"] == 3
    assert f["ticket"] == 5 and f["undetermined"] == 1
    assert f["layers"]["tester"] == 3


def test_week_meta_monday_start():
    # 2026-11-24 是周二，归到 11-23 那一周
    key, label, start = dw._week_meta(dt.datetime(2026, 11, 24, 9, 0))
    assert start == "2026-11-23"
    assert label == "11/23–11/29"
    # 周日同属这一周
    assert dw._week_meta(dt.datetime(2026, 11, 29, 23, 0))[2] == "2026-11-23"
    # 周一换周
    assert dw._week_meta(dt.datetime(2026, 11, 30, 0, 1))[2] == "2026-11-30"


def test_answer_metrics_weekly_buckets(monkeypatch):
    """按 at 归周；直答率只算已判定三层；本周打 partial。"""
    now = dt.datetime(2026, 11, 24, 10, 0)   # 周二
    rows = [
        _row("answered", "直答正确", "2026-11-17 10:00:00"),   # 上周
        _row("unanswered", "未直答", "2026-11-18 10:00:00"),   # 上周
        _row("answered", "直答正确", "2026-11-24 09:00:00"),   # 本周
        _row("ticket", "", "2026-11-17 12:00:00"),             # 上周，旁支
    ]
    # _answer_metrics 里是裸 import dar_studio（脚本运行时 ai/scripts 在 sys.path），
    # 单测里用 sys.modules 塞一个假模块，保证拿到同一份 _seg_rows
    fake = types.ModuleType("dar_studio")
    fake._seg_rows = lambda env: (rows, {})
    monkeypatch.setitem(sys.modules, "dar_studio", fake)

    am = dw._answer_metrics("prod", l2_weeks=2, funnel_weeks=8, now=now)
    assert am is not None
    weeks = {w["week"]: w for w in am["funnel_weekly"]}
    assert weeks["2026-W47"]["partial"] is False
    assert weeks["2026-W47"]["funnel"]["judged"] == 2       # 11-17 两条已判定
    assert weeks["2026-W47"]["funnel"]["ticket"] == 1
    # 本周（W48）还没过完，不进周桶
    assert "2026-W48" not in weeks
    # 累计：已判定 3 段里 2 段直答正确
    assert am["l2"]["L2_人工"] == "66.7%（2/3）"
    # 近 8 周趋势按时间正序
    assert [w["from"] for w in am["weekly"]] == sorted(w["from"] for w in am["weekly"])
