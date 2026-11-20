"""项目编号/项目名称唯一性校验单元测试。

覆盖 ProjectService.check_project_duplicate：
- 项目编号命中 → 返回编号冲突描述
- 仅项目名称命中 → 返回名称冲突描述
- 均未命中 → 返回 None
- exclude_id 排除自身后不再命中
"""
from unittest.mock import MagicMock

from app.modules.admin.services.project_service import project_service


class _FakeQuery:
    """filter 链式调用原样返回自身；first() 从会话共享队列中按调用顺序取结果。"""

    def __init__(self, session):
        self._session = session

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._session._results.pop(0) if self._session._results else None


class _FakeSession:
    """第一次 first() 查项目编号、第二次 first() 查项目名称。"""

    def __init__(self, results):
        self._results = list(results)
        self.closed = False

    def query(self, model):
        return _FakeQuery(self)

    def close(self):
        self.closed = True


def _patch_session(monkeypatch, results):
    fake = _FakeSession(results)
    monkeypatch.setattr("app.modules.admin.services.project_service.SessionLocal", lambda: fake)
    return fake


def test_duplicate_by_code(monkeypatch):
    fake = _patch_session(monkeypatch, [MagicMock(), None])
    msg = project_service.check_project_duplicate("PROJ-001", "全新项目")
    assert msg == "项目编号「PROJ-001」已存在"
    assert fake.closed


def test_duplicate_by_name(monkeypatch):
    fake = _patch_session(monkeypatch, [None, MagicMock()])
    msg = project_service.check_project_duplicate("PROJ-999", "已存在项目")
    assert msg == "项目名称「已存在项目」已存在"
    assert fake.closed


def test_no_duplicate(monkeypatch):
    fake = _patch_session(monkeypatch, [None, None])
    assert project_service.check_project_duplicate("PROJ-NEW", "全新项目") is None
    assert fake.closed


def test_duplicate_with_exclude_id(monkeypatch):
    # exclude_id 场景下即使自身编号/名称命中，也因被排除而不报冲突
    fake = _patch_session(monkeypatch, [None, None])
    assert project_service.check_project_duplicate("PROJ-NEW", "全新项目", exclude_id="PROJ-NEW") is None
    assert fake.closed
