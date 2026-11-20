"""WS 会话生命周期：welcome / 已读上报必须「用完即结束事务」。

背景（2026-10 发版失败事故）：ws_task_room 曾把同一个 Session 复用在整条 WS
连接上，只读查询（users / task_comment_read / task_comment_read_record）不提交，
事务与表级元数据锁一直挂到连接断开。部署补列时 ALTER TABLE 排不到锁，
lock_wait_timeout 到点报 1205，整次发版中止。

这里用假会话锁住两条不变量：
  1. 每次查询结束都显式 rollback（只读事务收尾，立刻释放 MDL）；
  2. 会话不跨帧存活，用完即关。
"""
import pytest

from app.modules.tasks.api import ws as ws_mod


class _FakeSession:
    """只记录事务收尾动作的假 Session（支持 with 语法）。"""

    def __init__(self):
        self.rollbacks = 0
        self.exits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.exits += 1
        return False

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def test_welcome_snapshot_ends_transaction(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(ws_mod, "SessionLocal", lambda: session)
    monkeypatch.setattr(ws_mod, "read_cursor_map", lambda db, task_id: {"alice": 3})
    monkeypatch.setattr(
        ws_mod, "read_records_map", lambda db, task_id, comment_ids, limit: {"1": []}
    )

    snapshot = ws_mod._welcome_snapshot(42)

    assert snapshot == {"read_map": {"alice": 3}, "read_records": {"1": []}}
    assert session.rollbacks == 1  # 只读事务显式收尾
    assert session.exits == 1      # 会话不跨帧复用


def test_welcome_snapshot_ends_transaction_even_on_query_error(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(ws_mod, "SessionLocal", lambda: session)

    def _boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(ws_mod, "read_cursor_map", _boom)
    monkeypatch.setattr(ws_mod, "read_records_map", _boom)

    snapshot = ws_mod._welcome_snapshot(42)

    assert snapshot == {"read_map": {}, "read_records": {}}
    assert session.rollbacks == 1
    assert session.exits == 1


def test_report_read_once_uses_short_session(monkeypatch):
    session = _FakeSession()
    monkeypatch.setattr(ws_mod, "SessionLocal", lambda: session)
    captured = {}

    def _fake_report(db, task_id, comment_ids, username, name=None,
                     avatar_resource_id=None, last_read_comment_id=None):
        captured.update(db=db, task_id=task_id, comment_ids=comment_ids, username=username)
        return {"records": [], "comment_ids": [], "last_read_comment_id": None}

    monkeypatch.setattr(ws_mod, "report_read_sync", _fake_report)

    result = ws_mod._report_read_once(7, [1, 2], "alice", "Alice", 9, 2)

    assert captured["db"] is session
    assert captured["task_id"] == 7
    assert captured["comment_ids"] == [1, 2]
    assert session.exits == 1  # 上报完立刻关闭，不留给下一帧
    assert result == {"records": [], "comment_ids": [], "last_read_comment_id": None}


def test_report_read_once_propagates_error(monkeypatch):
    """写入失败必须抛给调用方（由 ws_task_room 记日志并跳过广播）。"""
    session = _FakeSession()
    monkeypatch.setattr(ws_mod, "SessionLocal", lambda: session)

    def _fail(*args, **kwargs):
        raise RuntimeError("duplicate key")

    monkeypatch.setattr(ws_mod, "report_read_sync", _fail)

    with pytest.raises(RuntimeError):
        ws_mod._report_read_once(7, [1], "alice", None, None, None)
    assert session.exits == 1
