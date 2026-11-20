"""dispatch_text：四栏向量文本 / skip_empty / 描述截断 / 空值处理。

不调 LLM、不连库。
运行（仓库根）：
    pytest ai/agents/AiDiagnosisPlatform/assigner/tests/test_dispatch_text.py -v
"""

import pytest

from ai.agents.AiDiagnosisPlatform.assigner.recall.dispatch_text import (
    build_dispatch_ticket_text,
    _slot,
    _DESC_MAX,
)


class TestBuildDispatchTicketText:
    """build_dispatch_ticket_text：A 路入库/检索四栏拼接。"""

    def test_all_slots_filled(self):
        """正常流程：四栏都有值。"""
        text = build_dispatch_ticket_text(
            title="车辆故障",
            description="车停了",
            robot_type="S20",
            fault_code="E1001",
        )
        assert "标题：车辆故障" in text
        assert "描述：车停了" in text
        assert "车型：S20" in text
        assert "故障码：E1001" in text
        assert text.count("\n") == 3

    def test_empty_slots_write_wu(self):
        """正常流程：空值写「无」（skip_empty=False 默认）。"""
        text = build_dispatch_ticket_text(title="test", description="desc")
        assert "车型：无" in text
        assert "故障码：无" in text

    def test_skip_empty_omits_empty_slots(self):
        """正常流程：skip_empty=True 时空车型/故障码不写栏。"""
        text = build_dispatch_ticket_text(
            title="test", description="desc", skip_empty=True,
        )
        assert "车型" not in text
        assert "故障码" not in text

    def test_skip_empty_keeps_filled_slots(self):
        """正常流程：skip_empty=True 但有值仍写出。"""
        text = build_dispatch_ticket_text(
            title="t", description="d",
            robot_type="S20", fault_code="E1001",
            skip_empty=True,
        )
        assert "车型：S20" in text
        assert "故障码：E1001" in text

    def test_description_truncated_at_300(self):
        """边界条件：描述超过 300 字截断。"""
        long_desc = "长" * 400
        text = build_dispatch_ticket_text(title="t", description=long_desc)
        lines = text.splitlines()
        desc_line = [ln for ln in lines if ln.startswith("描述：")][0]
        assert len(desc_line) == len("描述：") + _DESC_MAX

    def test_description_exactly_300_not_truncated(self):
        """边界条件：描述恰好 300 字不截断。"""
        desc = "x" * 300
        text = build_dispatch_ticket_text(title="t", description=desc)
        lines = text.splitlines()
        desc_line = [ln for ln in lines if ln.startswith("描述：")][0]
        assert len(desc_line) == len("描述：") + 300

    def test_none_title_becomes_wu(self):
        """异常流程：title=None → 写「无」。"""
        text = build_dispatch_ticket_text(title=None, description="d")
        assert "标题：无" in text

    def test_whitespace_only_title_becomes_wu(self):
        """异常流程：title 纯空白 → 写「无」。"""
        text = build_dispatch_ticket_text(title="   ", description="d")
        assert "标题：无" in text

    def test_all_none(self):
        """异常流程：所有参数 None → 四栏全「无」。"""
        text = build_dispatch_ticket_text()
        assert "标题：无" in text
        assert "描述：无" in text
        assert "车型：无" in text
        assert "故障码：无" in text


class TestSlot:
    """_slot 内部函数。"""

    def test_value_with_limit(self):
        assert _slot("abc", limit=2) == "ab"

    def test_empty_returns_wu(self):
        assert _slot("") == "无"
        assert _slot(None) == "无"
        assert _slot("  ") == "无"

    def test_strips_whitespace(self):
        assert _slot("  hello  ") == "hello"

    def test_no_limit_no_truncation(self):
        assert _slot("long text no limit") == "long text no limit"
