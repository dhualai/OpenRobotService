"""SyncEngine 状态合并单元测试（INTEGRATION_DESIGN.md §4.2）。

单向同步、取较后状态：外部源与本平台比状态序号取 max；
本平台已领先/同级时不操作（外部允许滞后）。
"""
import pytest

from app.integrations.engine import STATUS_ORD, merge_status
from app.models.task import TaskStatus


def test_status_ord_definition():
    assert STATUS_ORD[TaskStatus.NEW] == 0
    assert STATUS_ORD[TaskStatus.IN_PROGRESS] == 1
    assert STATUS_ORD[TaskStatus.PENDING] == 1   # 与 in_progress 同级
    assert STATUS_ORD[TaskStatus.RESOLVED] == 2
    assert STATUS_ORD[TaskStatus.CANCELED] == 3  # 与 closed 同级
    assert STATUS_ORD[TaskStatus.CLOSED] == 3


@pytest.mark.parametrize("local,incoming,expected", [
    # 外部领先 → 同步推进
    (TaskStatus.NEW, TaskStatus.IN_PROGRESS, TaskStatus.IN_PROGRESS),
    (TaskStatus.NEW, TaskStatus.RESOLVED, TaskStatus.RESOLVED),
    (TaskStatus.NEW, TaskStatus.CANCELED, TaskStatus.CANCELED),
    (TaskStatus.IN_PROGRESS, TaskStatus.CLOSED, TaskStatus.CLOSED),
    (TaskStatus.IN_PROGRESS, TaskStatus.CANCELED, TaskStatus.CANCELED),
    (TaskStatus.RESOLVED, TaskStatus.CLOSED, TaskStatus.CLOSED),
    (TaskStatus.RESOLVED, TaskStatus.CANCELED, TaskStatus.CANCELED),
    # 本平台领先 → 不动（禅道允许滞后）
    (TaskStatus.CLOSED, TaskStatus.NEW, None),
    (TaskStatus.CLOSED, TaskStatus.RESOLVED, None),
    (TaskStatus.CLOSED, TaskStatus.CANCELED, None),
    (TaskStatus.CANCELED, TaskStatus.NEW, None),
    (TaskStatus.CANCELED, TaskStatus.RESOLVED, None),
    (TaskStatus.CANCELED, TaskStatus.CLOSED, None),
    (TaskStatus.RESOLVED, TaskStatus.IN_PROGRESS, None),
    (TaskStatus.IN_PROGRESS, TaskStatus.NEW, None),
    # 同级 / 同态 → 不动
    (TaskStatus.IN_PROGRESS, TaskStatus.PENDING, None),   # 暂停不算前进
    (TaskStatus.PENDING, TaskStatus.IN_PROGRESS, None),
    (TaskStatus.NEW, TaskStatus.NEW, None),
    (TaskStatus.CLOSED, TaskStatus.CLOSED, None),
    (TaskStatus.CANCELED, TaskStatus.CANCELED, None),
])
def test_merge_status(local, incoming, expected):
    assert merge_status(local, incoming) == expected
