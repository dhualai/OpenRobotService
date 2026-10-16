"""工单点赞与「精品工单榜」接口口径（活动中心）。

被测（app/modules/tasks/api/task.py）：
  - POST   /api/tasks/{task_id}/like   点赞（幂等）
  - DELETE /api/tasks/{task_id}/like   取消点赞（软删除，幂等）
  - GET    /api/tasks/{task_id}/like   我的点赞状态
  - GET    /api/tasks/featured         精品榜（只统计已归档，按有效点赞数倒序）

回归点一：「/featured」是静态段路径，一旦被注册到贪婪的 GET /{task_id} 之后，
请求会被当成 task_id="featured" 而 422 —— TC-FEAT-ROUTE 专门钉死这一点。

回归点二（红包激励一期 §8.3.1 / §12.8）：计数与榜单只认**有效点赞**
（is_active=1 且 risk_status='normal'），取消点赞为软删除（UPDATE，不 DELETE），
保证期末可核验、异常点赞可追溯。

一律不连真库：conftest 已把 app.core.database 换成 MagicMock，这里只 override
get_db（异步生成器）返回可编排的 AsyncMock。
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import mysql

from app.core.auth_routes import get_current_active_user_from_token
from app.models.task import TaskStatus
from app.modules.tasks.api.task import get_db, router
from app.modules.tasks.services import knowledge_review

USER = {"id": "u-bob", "username": "bob"}

TASK_ID = 1001


class _Res:
    """最小 Result 替身：覆盖路由用到的 scalars()/all()/first()/scalar_one*()。"""

    def __init__(self, rows=None, scalar=None, one=None):
        self._rows = list(rows or [])
        self._scalar = scalar
        self._one = one

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._one


def _task(**over):
    data = {
        "id": TASK_ID,
        "title": "机器人零点标定失败",
        "status": TaskStatus.ARCHIVED,
        "project_name": "项目A",
        "created_by": "alice",
        "created_at": datetime(2026, 9, 1, 9, 0, 0),
        "archived_at": datetime(2026, 9, 20, 18, 0, 0),
    }
    data.update(over)
    return SimpleNamespace(**data)


def _sql(stmt) -> str:
    """按 MySQL 方言把语句编译成文本，便于断言口径（ON DUPLICATE / 状态条件）。"""
    return str(stmt.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.fixture
def db():
    mock = MagicMock()
    mock.commit = AsyncMock()
    return mock


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(router, prefix="/api/tasks")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_user_from_token] = lambda: USER
    app.dependency_overrides[knowledge_review.raw_access_guard] = lambda: None
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_task_likes_preserve_blind_review_guard(method, db, monkeypatch):
    reviewer = {"id": "u-reviewer", "username": "reviewer"}
    report = SimpleNamespace(id=9, submit_status="submitted", reviewer="u-reviewer")
    db.execute = AsyncMock(return_value=_Res(one=report))
    monkeypatch.setattr(knowledge_review, "actor_identities", AsyncMock(return_value={"u-reviewer"}))
    app = FastAPI()
    app.include_router(router, prefix="/api/tasks")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_active_user_from_token] = lambda: reviewer
    app.dependency_overrides[knowledge_review.blind_review_actor] = lambda: reviewer
    with TestClient(app) as test_client:
        response = test_client.request(method, f"/api/tasks/{TASK_ID}/like")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "BLIND_REVIEW_REQUIRED"
    assert response.json()["detail"]["report_id"] == report.id
    assert db.execute.await_count == 1
    db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# TC-FEAT-ROUTE: /featured 不能被贪婪路由吞掉
# ---------------------------------------------------------------------------

def test_featured_route_is_static_segment(client, db):
    """/api/tasks/featured 必须命中静态段路由，而不是被 GET /{task_id} 当 id 解析。"""
    db.execute = AsyncMock(side_effect=[_Res(rows=[]), _Res(scalar=0)])

    response = client.get("/api/tasks/featured")

    assert response.status_code == 200, response.text
    assert response.json() == {"total": 0, "items": []}


def test_featured_query_limits_to_archived_and_joins_likes(client, db):
    """精品榜 SQL 必须 join task_likes，限定 tasks.status = 已归档，且只计有效点赞。"""
    db.execute = AsyncMock(side_effect=[_Res(rows=[]), _Res(scalar=0)])

    client.get("/api/tasks/featured?limit=5")

    stmt = db.execute.await_args_list[0].args[0]
    sql = _sql(stmt)
    assert "task_likes" in sql
    assert "archived" in sql.lower()
    assert "GROUP BY" in sql.upper()
    # 有效点赞口径：已取消 / 风控无效的点赞不进榜
    assert "is_active" in sql
    assert "risk_status" in sql
    assert "normal" in sql


def test_featured_total_counts_distinct_valid_liked_tasks(client, db):
    """榜单 total = 有效点赞过的已归档工单数（同样排除取消与风控无效）。"""
    db.execute = AsyncMock(side_effect=[_Res(rows=[]), _Res(scalar=0)])

    client.get("/api/tasks/featured")

    count_sql = _sql(db.execute.await_args_list[1].args[0])
    assert "task_likes" in count_sql
    assert "is_active" in count_sql
    assert "risk_status" in count_sql


def test_featured_marks_my_like_and_returns_counts(client, db):
    """榜单按点赞数倒序返回，并标出「我」点过哪些。"""
    db.execute = AsyncMock(side_effect=[
        _Res(rows=[(_task(id=1), 9), (_task(id=2), 4)]),
        _Res(rows=[(2,)]),
        _Res(scalar=2),
    ])

    body = client.get("/api/tasks/featured").json()

    assert body["total"] == 2
    assert [i["id"] for i in body["items"]] == [1, 2]
    assert [i["like_count"] for i in body["items"]] == [9, 4]
    assert [i["is_liked"] for i in body["items"]] == [False, True]
    assert body["items"][0]["status"] == "archived"
    assert body["items"][0]["title"] == "机器人零点标定失败"


# ---------------------------------------------------------------------------
# TC-LIKE: 点赞 / 取消 / 状态
# ---------------------------------------------------------------------------

def test_like_is_idempotent_and_returns_count(client, db):
    db.execute = AsyncMock(side_effect=[_Res(one=TASK_ID), _Res(), _Res(scalar=3)])

    body = client.post(f"/api/tasks/{TASK_ID}/like").json()

    assert body["liked"] is True
    assert body["like_count"] == 3
    # 多端并发点赞不撞唯一键：走 ON DUPLICATE KEY UPDATE，并恢复有效状态
    insert_sql = _sql(db.execute.await_args_list[1].args[0])
    assert "INSERT INTO task_likes" in insert_sql
    assert "ON DUPLICATE KEY UPDATE" in insert_sql.upper()
    assert "is_active" in insert_sql  # 取消后重新点赞复用同一行 → 恢复为有效
    # 返回的计数走有效点赞口径
    count_sql = _sql(db.execute.await_args_list[2].args[0])
    assert "is_active" in count_sql
    assert "risk_status" in count_sql


def test_like_404_when_task_missing(client, db):
    db.execute = AsyncMock(side_effect=[_Res(one=None)])

    response = client.post("/api/tasks/999999/like")

    assert response.status_code == 404


def test_unlike_is_idempotent_and_returns_count(client, db):
    db.execute = AsyncMock(side_effect=[_Res(), _Res(scalar=2)])

    body = client.delete(f"/api/tasks/{TASK_ID}/like").json()

    assert body["liked"] is False
    assert body["like_count"] == 2
    # 软删除：UPDATE is_active=0，绝不物理删行（期末核验 / 异常点赞追溯需要痕迹）
    unlike_sql = _sql(db.execute.await_args_list[0].args[0])
    assert "UPDATE task_likes" in unlike_sql
    assert "is_active" in unlike_sql
    assert "DELETE" not in unlike_sql.upper()


def test_get_like_reports_my_state(client, db):
    db.execute = AsyncMock(side_effect=[_Res(one=77), _Res(scalar=5)])

    body = client.get(f"/api/tasks/{TASK_ID}/like").json()

    assert body["liked"] is True
    assert body["like_count"] == 5
    # 我的状态只看 is_active（取消后回到未点赞）
    mine_sql = _sql(db.execute.await_args_list[0].args[0])
    assert "is_active" in mine_sql
    # 我的状态不受风控标记影响：被判无效仍显示已点赞，避免「按钮点不动」的死状态
    assert "risk_status" not in mine_sql


def test_get_like_reports_not_liked(client, db):
    db.execute = AsyncMock(side_effect=[_Res(one=None), _Res(scalar=0)])

    body = client.get(f"/api/tasks/{TASK_ID}/like").json()

    assert body["liked"] is False
    assert body["like_count"] == 0
