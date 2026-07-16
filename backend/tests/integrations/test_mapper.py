"""禅道 task → ExternalTask 映射单元测试（INTEGRATION_DESIGN.md §6）。"""
from datetime import datetime

import pytest

from app.integrations.sources.zentao import mapper
from app.models.task import TaskPriority, TaskStatus, TaskType

# 取自 candao_dev/需求描述.md 的真实禅道 task 样例（精简保留关键字段）
SAMPLE = {
    "id": 3288,
    "project": 236,
    "parent": 0,
    "executi1n": 237,
    "name": "摇人吧-初版github开源上线，完成功能联调及测试",
    "type": "devel",
    "pri": 1,
    "estimate": 16,
    "consumed": 0,
    "left": 16,
    "deadline": "2026-07-18",
    "status": "wait",
    "desc": "",
    "openedBy": {"id": 31, "account": "zhangjunlei", "realname": "张俊磊"},
    "openedDate": "2026-07-14T06:13:37Z",
    "assignedTo": {"id": 31, "account": "zhangjunlei", "realname": "张俊磊"},
}


def test_full_sample_mapping():
    ext = mapper.zentao_task_to_external(SAMPLE, base_url="http://zentao.example.com")

    assert ext.external_id == "3288"
    assert ext.title == SAMPLE["name"]
    # desc 为空 → description 回退用标题
    assert ext.description == SAMPLE["name"]
    assert ext.status == TaskStatus.NEW            # wait → new
    assert ext.priority == TaskPriority.URGENT     # pri 1 → urgent
    assert ext.task_type == TaskType.FEATURE       # devel → feature
    assert ext.assigned_account == "zhangjunlei"
    assert ext.created_account == "zhangjunlei"

    # openedDate（ISO，含 Z）解析
    assert (ext.created_at.year, ext.created_at.month, ext.created_at.day,
            ext.created_at.hour) == (2026, 7, 14, 6)
    # deadline（仅日期）解析
    assert ext.deadline_at == datetime(2026, 7, 18)

    assert ext.url == "http://zentao.example.com/task-view-3288.html"

    # 工时与层级信息落入 extra
    assert ext.extra["estimate"] == 16
    assert ext.extra["consumed"] == 0
    assert ext.extra["left"] == 16
    assert ext.extra["execution_id"] == 237          # 取自 executi1n 笔误字段
    assert ext.extra["zentao_project_id"] == 236
    assert ext.extra["zentao_pri"] == 1
    assert ext.extra["zentao_type"] == "devel"
    assert ext.extra["assigned_realname"] == "张俊磊"
    assert ext.extra["opened_realname"] == "张俊磊"


def test_desc_non_empty_used_as_description():
    t = dict(SAMPLE, desc="详细描述内容")
    ext = mapper.zentao_task_to_external(t)
    assert ext.description == "详细描述内容"


def test_url_empty_when_no_base():
    ext = mapper.zentao_task_to_external(SAMPLE, base_url="")
    assert ext.url == ""


@pytest.mark.parametrize("zentao,expected", [
    ("wait", TaskStatus.NEW),
    ("doing", TaskStatus.IN_PROGRESS),
    ("pause", TaskStatus.PENDING),
    ("done", TaskStatus.RESOLVED),
    ("cancel", TaskStatus.CLOSED),
    ("closed", TaskStatus.CLOSED),
    ("", TaskStatus.NEW),
    (None, TaskStatus.NEW),
    ("unknown", TaskStatus.NEW),
])
def test_map_status(zentao, expected):
    assert mapper.map_status(zentao) == expected


@pytest.mark.parametrize("pri,expected", [
    (1, TaskPriority.URGENT),
    (2, TaskPriority.HIGH),
    (3, TaskPriority.MEDIUM),
    (4, TaskPriority.LOW),
    ("2", TaskPriority.HIGH),    # 字符串数字
    (5, TaskPriority.MEDIUM),    # 超出范围 → 默认 medium
    (None, TaskPriority.MEDIUM),
    ("abc", TaskPriority.MEDIUM),
])
def test_map_priority(pri, expected):
    assert mapper.map_priority(pri) == expected


@pytest.mark.parametrize("t,expected", [
    ("devel", TaskType.FEATURE),
    ("test", TaskType.SUPPORT),
    ("design", TaskType.OTHER),
    ("research", TaskType.OTHER),
    ("misc", TaskType.OTHER),
    ("", TaskType.OTHER),
    (None, TaskType.OTHER),
    ("something-new", TaskType.OTHER),
])
def test_map_task_type(t, expected):
    assert mapper.map_task_type(t) == expected


def test_assigned_to_as_string_or_none():
    # 字符串形式
    ext = mapper.zentao_task_to_external(dict(SAMPLE, assignedTo="zhangjunlei"))
    assert ext.assigned_account == "zhangjunlei"
    # None
    ext = mapper.zentao_task_to_external(dict(SAMPLE, assignedTo=None))
    assert ext.assigned_account is None
