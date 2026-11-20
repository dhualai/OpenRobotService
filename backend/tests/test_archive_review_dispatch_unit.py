"""归档派审桥接：不连库的纯函数单测。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.tasks.services.archive_review_dispatch import (
    assignment_log_detail,
    expand_exclude_ids,
)


class _FakeAssignment:
    domain = "调度系统"
    reviewer_id = "rev_b"
    candidate_ids = ["rev_a", "rev_b"]
    load_snapshot = {"rev_a": 2, "rev_b": 0}
    probability_snapshot = {"rev_a": 0.3333, "rev_b": 0.6667}
    reason = "demo"


def test_assignment_log_detail_shape():
    detail = assignment_log_detail(_FakeAssignment())
    assert detail["domain"] == "调度系统"
    assert detail["reviewer"] == "rev_b"
    assert detail["source"] == "archive_review_dispatch"
    assert detail["load_snapshot"]["rev_b"] == 0
    assert detail["probability_snapshot"]["rev_b"] == 0.6667


@pytest.mark.asyncio
async def test_expand_exclude_ids_merges_openid_and_user_id(monkeypatch):
    """openid 与 users.id 互认：脱敏集合应同时含两者。"""
    monkeypatch.setattr(
        "app.core.user_identity.identity_keys",
        lambda raw: [raw, "biz-user-1", "openid-wx-1"] if raw else [],
    )

    row = SimpleNamespace(id="biz-user-1", username="zhangsan", wechat_openid="openid-wx-1")
    result = MagicMock()
    result.all.return_value = [row]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    keys = await expand_exclude_ids(db, ["openid-wx-1"])
    assert "openid-wx-1" in keys
    assert "biz-user-1" in keys
    assert "zhangsan" in keys


@pytest.mark.asyncio
async def test_expand_exclude_ids_empty():
    db = AsyncMock()
    assert await expand_exclude_ids(db, []) == []
    assert await expand_exclude_ids(db, None) == []
