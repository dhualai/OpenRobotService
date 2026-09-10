# -*- coding: utf-8 -*-
"""lookup_ticket 无号形态（我的工单清单）格式化块的单测。"""
from ai.agents.AiDiagnosisPlatform.pipeline import (
    AiDiagnosisPlatform,
    _TICKET_STATUS_CN,
)

fmt = AiDiagnosisPlatform._format_user_tickets_block

_ROWS = [
    {"id": 632, "title": "电梯外设频繁离线", "status": "IN_PROGRESS",
     "project_name": "某仓储项目", "created_at": "2026-09-08 10:00:00",
     "is_assignee_only": False},
    {"id": 606, "title": "用户统计日期范围超7天报错", "status": "RESOLVED",
     "project_name": None, "created_at": "2026-09-05 08:00:00",
     "is_assignee_only": True},
]


def test_empty_open_and_all():
    assert "暂无待处理" in fmt([], 0, True)
    assert "暂无名下工单" in fmt([], 0, False) or "暂无工单" in fmt([], 0, False)
    assert "不要编造" in fmt([], 0, True)


def test_fields_status_cn_and_role_mark():
    b = fmt(_ROWS, 2, False)
    assert "#632｜电梯外设频繁离线｜处理中｜某仓储项目｜09/08" in b
    assert "#606｜用户统计日期范围超7天报错｜已解决（待提单人确认关闭）｜09/05｜（您是接单人）" in b
    assert "共 2 张工单" in b and "已全部列出" in b
    assert "③这是工单清单查询的全部能力" in b


def test_truncation_wording():
    b = fmt(_ROWS[:1], 35, False)
    assert "共 35 张工单" in b and "仅展示最近 1 张" in b


def test_only_open_scope_header():
    assert "待处理" in fmt(_ROWS, 2, True)


def test_status_map_has_canceled():
    assert _TICKET_STATUS_CN.get("canceled") == "已取消"


def test_long_title_and_project_caps():
    b = fmt([{"id": 1, "title": "标" * 60, "status": "new",
              "project_name": "项" * 30,
              "created_at": "2026-09-01 00:00:00",
              "is_assignee_only": False}], 1, False)
    assert "标" * 40 in b and "标" * 41 not in b
    assert "项" * 20 in b and "项" * 21 not in b
