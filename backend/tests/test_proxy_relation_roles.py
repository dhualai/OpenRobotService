"""代他人提单（代理提单）：接单人视角标记 + 协商/流转权限对齐 的 API 处理器级覆盖。

需求方案：`docs/PRODUCT/代他人提单（代理提单）功能设计方案.md` §3.4

本套件刻意隔离 MySQL / 通知 / AI / WebSocket，只验证「角色 → 权限 → 字段归属」这段逻辑：

1. **接单人视角标记**（需求 1）
   - `_proxy_relation_response`：接单人可见 `is_assignee`，且代理人/被代理人姓名照常下发；
   - `TicketService._attach_proxy_relations`：列表卡片回填 `is_proxy_assignee`，
     非参与人仍走脱敏分支（姓名置 None）。

2. **协商与状态流转对齐**（需求 2）
   - 被代理人（acknowledged）归 creator 侧：可 respond、可改状态、可打回；
   - 被代理人（pending / declined）只读：改状态 / respond / 打回 一律 403；
   - `complete-step` 收紧：提单人与（已确认）被代理人**不可**替处理人推进阶段；
   - 回合归属 `step_last_updated_by` 按真实操作人侧别记录，不再硬编码。

说明：handler 直调（`asyncio.run`）而非 TestClient，避免 response_model 序列化噪音，
且不依赖 allure / pytest-asyncio（本地 venv 未装，见 AGENT.md 的 `--ignore=tests/tasks`）。
"""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.ticket_roles import TicketRoles
from app.modules.tasks.api import task as task_api
from app.modules.tasks.api.task import (
    CompleteStepRequest,
    ReopenStepRequest,
    RespondRequest,
    SetStepTimeRequest,
    _proxy_relation_response,
)
from app.modules.tasks.models.ticket import TicketStatus
from app.modules.tasks.services.proxy_relation_service import ProxyRelationService
from app.modules.tasks.services.ticket_service import TicketService

# ---------------------------------------------------------------------------
# 身份：代理人（= created_by）/ 被代理人 / 接单人（= assigned_to）/ 无关路人
# ---------------------------------------------------------------------------
AGENT = {"id": "u-agent", "username": "agent-01", "is_admin": False, "permissions": []}
PRINCIPAL = {"id": "u-principal", "username": "principal-01", "is_admin": False, "permissions": []}
ASSIGNEE = {"id": "u-assignee", "username": "engineer-01", "is_admin": False, "permissions": []}
STRANGER = {"id": "u-stranger", "username": "stranger-01", "is_admin": False, "permissions": []}

TASK_ID = 2001


# ---------------------------------------------------------------------------
# 最小替身
# ---------------------------------------------------------------------------
class _FakeResult:
    """`db.execute(...)` 的返回替身，覆盖 .unique().scalar_one_or_none() / .scalars().all()。"""

    def __init__(self, value=None):
        self._value = value

    def unique(self):
        return self

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value or []


class _FakeSession:
    """最小 AsyncSession 替身：按调用顺序弹出 execute 结果。"""

    def __init__(self, *results):
        self._results = list(results)
        self.commits = 0
        self.refreshed = []

    async def execute(self, *args, **kwargs):
        return _FakeResult(self._results.pop(0) if self._results else None)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    async def refresh(self, obj=None):
        self.refreshed.append(obj)

    async def flush(self):
        pass


def _ticket(**overrides) -> SimpleNamespace:
    """代理提单工单：created_by=代理人，assigned_to=接单人。"""
    data = {
        "id": TASK_ID,
        "title": "机器人无法启动",
        "description": "启动后显示故障码 E1001",
        "ticket_type": "problem",
        "task_type": "problem",
        "priority": "high",
        "status": TicketStatus.IN_PROGRESS,
        "source": "manual",
        "created_by": "agent-01",      # 代理人
        "assigned_to": "engineer-01",  # 接单人
        "customer": None,
        "team": None,
        "project_name": "测试项目",
        "project_id": "project-01",
        "related_resource_id": None,
        "tags": [],
        "metadata_info": None,
        "attachments": [],
        "created_at": datetime(2026, 9, 1, 10, 0, 0),
        "updated_at": datetime(2026, 9, 2, 10, 0, 0),
        "resolved_at": None,
        "closed_at": None,
        "deadline_at": None,
        "reply_count": 0,
        "view_count": 5,
        "comments": [],
        # 阶段性处理 / 协商
        "curr_step_id": 11,
        "curr_step_name": "现场排查",
        "curr_step_endtime": datetime(2026, 9, 5, 10, 0, 0),
        "curr_step_agreed": False,
        "step_last_updated_by": None,
        "step_last_updated_at": None,
        "step_negotiation_round": 0,
        "step_phase_round": 0,
        "step_neg_max_rounds": 5,
        "escalate_count": 0,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _relation(status: str = "acknowledged", **overrides) -> SimpleNamespace:
    data = {
        "id": 501,
        "task_id": TASK_ID,
        "relation_status": status,
        "source": "manual",
        "remark": None,
        "agent_id": "agent-01",
        "agent_username": "agent-01",
        "principal_id": "principal-01",
        "principal_username": "principal-01",
        "notified_at": datetime(2026, 9, 1, 10, 0, 0),
        "acked_at": datetime(2026, 9, 1, 11, 0, 0) if status == "acknowledged" else None,
        "declined_at": datetime(2026, 9, 1, 11, 0, 0) if status == "declined" else None,
        "created_at": datetime(2026, 9, 1, 10, 0, 0),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _step(step_id: int, sequence: int, name: str, task_type: str = "problem") -> SimpleNamespace:
    return SimpleNamespace(id=step_id, sequence=sequence, step_name=name, task_type=task_type)


# ---------------------------------------------------------------------------
# 夹具：把副作用（日志 / 评论 / WS / 响应重载）与关系查询全部打桩
# ---------------------------------------------------------------------------
@pytest.fixture
def sentinel() -> SimpleNamespace:
    return SimpleNamespace(name="reloaded-ticket")


@pytest.fixture
def side_effects(monkeypatch, sentinel):
    """打桩所有副作用，避免触库 / 触网；返回 sentinel 供断言返回值。"""
    monkeypatch.setattr(task_api.OperationLogService, "log", AsyncMock())
    monkeypatch.setattr(task_api, "_add_system_comment", AsyncMock())
    monkeypatch.setattr(task_api, "ws_broadcast_task_updated", AsyncMock())
    monkeypatch.setattr(
        task_api, "_reload_ticket_with_comments", AsyncMock(return_value=sentinel)
    )
    # PATCH /status 的关联策略链路（阻塞校验 / 重复单同步）一并短路
    monkeypatch.setattr(task_api, "get_all_policies", AsyncMock(return_value=None))
    monkeypatch.setattr(task_api, "_check_relation_block", AsyncMock(return_value=[]))
    monkeypatch.setattr(task_api, "_sync_duplicate_status", AsyncMock(return_value=0))
    return sentinel


@pytest.fixture
def relation_holder(monkeypatch):
    """注入代理关系（`load_relation` 会经 ProxyRelationService 读取）。"""
    holder = {"relation": None}

    async def _get_task_relation(db, task_id):
        return holder["relation"]

    monkeypatch.setattr(ProxyRelationService, "get_task_relation", _get_task_relation)
    return holder


def _wire_ticket(monkeypatch, ticket):
    monkeypatch.setattr(TicketService, "get_ticket_by_id", AsyncMock(return_value=ticket))
    return ticket


def _expect_http_error(coro, status_code: int) -> HTTPException:
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(coro)
    assert exc_info.value.status_code == status_code, exc_info.value.detail
    return exc_info.value


# ===========================================================================
# 需求 1：接单人可见「谁代谁提单」
# ===========================================================================
def test_proxy_relation_response_flags_assignee_and_keeps_names():
    """接单人视角：is_assignee=True，且代理双方姓名照常下发（接单人本就是参与人）。"""
    relation = _relation("acknowledged")
    roles = TicketRoles(is_assignee=True, is_related=True, side="assigned")

    resp = _proxy_relation_response(relation, roles)

    assert resp.is_assignee is True
    assert resp.is_agent is False
    assert resp.is_principal is False
    assert resp.relation_status == "acknowledged"
    # user_map 未命中时回落到 relation 里的 username（不落空）
    assert resp.agent_name == "agent-01"
    assert resp.principal_name == "principal-01"


def test_proxy_relation_response_keeps_agent_and_principal_views():
    """原有两种视角不被接单人标记影响。"""
    relation = _relation("acknowledged")

    as_agent = _proxy_relation_response(relation, TicketRoles(is_agent=True, is_related=True))
    assert as_agent.is_agent is True and as_agent.is_assignee is False

    as_principal = _proxy_relation_response(
        relation, TicketRoles(is_principal=True, is_related=True)
    )
    assert as_principal.is_principal is True and as_principal.is_assignee is False


def test_attach_proxy_relations_flags_assignee_with_names():
    """列表卡片：接单人拿到 is_proxy_assignee + 双方姓名。"""
    ticket = _ticket()
    db = _FakeSession([_relation("acknowledged")])

    asyncio.run(
        TicketService._attach_proxy_relations(
            db, [ticket], {"agent-01": "张三", "principal-01": "李四"}, "engineer-01"
        )
    )

    assert ticket.is_proxy_assignee is True
    assert ticket.is_proxy_agent is False
    assert ticket.is_principal is False
    assert ticket.proxy_relation_status == "acknowledged"
    assert ticket.proxy_agent_name == "张三"
    assert ticket.proxy_principal_name == "李四"


def test_attach_proxy_relations_masks_names_for_stranger():
    """脱敏不变：非参与人既不是接单人，也拿不到双方姓名。"""
    ticket = _ticket()
    db = _FakeSession([_relation("acknowledged")])

    asyncio.run(
        TicketService._attach_proxy_relations(
            db, [ticket], {"agent-01": "张三", "principal-01": "李四"}, "stranger-01"
        )
    )

    assert ticket.is_proxy_assignee is False
    assert ticket.is_proxy_agent is False
    assert ticket.is_principal is False
    assert ticket.proxy_agent_name is None
    assert ticket.proxy_principal_name is None


# ===========================================================================
# 需求 2-A：respond —— 被代理人可协商，pending/declined 只读
# ===========================================================================
def test_respond_allows_acknowledged_principal_and_records_creator_side(
    monkeypatch, side_effects, relation_holder
):
    """已确认跟进的被代理人可确认协商节点，且回合归属记为 creator 侧。"""
    ticket = _wire_ticket(monkeypatch, _ticket())
    relation_holder["relation"] = _relation("acknowledged")
    db = _FakeSession(_step(11, 1, "现场排查"))

    result = asyncio.run(
        task_api.respond_task(
            task_id=TASK_ID,
            body=RespondRequest(curr_step_id=11),
            db=db,
            current_user=PRINCIPAL,
            request=None,
        )
    )

    assert result is side_effects
    assert ticket.curr_step_agreed is True
    assert ticket.step_last_updated_by == "creator"
    assert db.commits == 1


@pytest.mark.parametrize("status", ["pending", "declined"])
def test_respond_rejects_non_acknowledged_principal(
    monkeypatch, side_effects, relation_holder, status
):
    """pending（只读态）/ declined（关系终态）的被代理人一律 403。"""
    _wire_ticket(monkeypatch, _ticket())
    relation_holder["relation"] = _relation(status)

    _expect_http_error(
        task_api.respond_task(
            task_id=TASK_ID,
            body=RespondRequest(curr_step_id=11),
            db=_FakeSession(_step(11, 1, "现场排查")),
            current_user=PRINCIPAL,
            request=None,
        ),
        403,
    )


@pytest.mark.parametrize(
    "user,expected_side", [(ASSIGNEE, "assigned"), (AGENT, "creator")]
)
def test_respond_records_real_actor_side(
    monkeypatch, side_effects, relation_holder, user, expected_side
):
    """回合归属按真实操作人侧别记录（接单人 assigned / 代理人 creator）。"""
    ticket = _wire_ticket(monkeypatch, _ticket())
    relation_holder["relation"] = _relation("acknowledged")

    asyncio.run(
        task_api.respond_task(
            task_id=TASK_ID,
            body=RespondRequest(curr_step_id=11),
            db=_FakeSession(_step(11, 1, "现场排查")),
            current_user=user,
            request=None,
        )
    )

    assert ticket.step_last_updated_by == expected_side


# ===========================================================================
# 需求 2-B：PATCH /status —— 已确认被代理人放行，pending 仍只读
# ===========================================================================
def test_update_status_allows_acknowledged_principal(monkeypatch, side_effects, relation_holder):
    """已确认跟进的被代理人可改状态（原来双键直比会误判 403）。"""
    ticket = _wire_ticket(monkeypatch, _ticket())
    relation_holder["relation"] = _relation("acknowledged")
    update_mock = AsyncMock(return_value=ticket)
    monkeypatch.setattr(TicketService, "update_ticket_status", update_mock)

    result = asyncio.run(
        task_api.update_task_status(
            task_id=TASK_ID, status="in_progress", db=_FakeSession(), current_user=PRINCIPAL,
            request=None,
        )
    )

    assert result is side_effects
    update_mock.assert_awaited_once()


def test_update_status_rejects_pending_principal(monkeypatch, side_effects, relation_holder):
    """未确认跟进的被代理人仍只读：改状态 403。"""
    _wire_ticket(monkeypatch, _ticket())
    relation_holder["relation"] = _relation("pending")

    _expect_http_error(
        task_api.update_task_status(
            task_id=TASK_ID, status="in_progress", db=_FakeSession(), current_user=PRINCIPAL,
            request=None,
        ),
        403,
    )


def test_update_status_withdraw_still_requires_creator(monkeypatch, side_effects, relation_holder):
    """撤回语义不变：即便被代理人已确认跟进，也不能替代理人撤回（仅 created_by）。"""
    _wire_ticket(monkeypatch, _ticket())
    relation_holder["relation"] = _relation("acknowledged")

    err = _expect_http_error(
        task_api.update_task_status(
            task_id=TASK_ID, status="canceled", db=_FakeSession(), current_user=PRINCIPAL,
            request=None,
        ),
        403,
    )
    assert "撤回" in err.detail


# ===========================================================================
# 需求 2-C：complete-step 收紧 —— 不可回填 roles.can_operate
# ===========================================================================
def _complete_step_body() -> CompleteStepRequest:
    return CompleteStepRequest(
        next_step_id=12, curr_step_endtime=datetime(2026, 9, 8, 10, 0, 0)
    )


@pytest.mark.parametrize("user", [PRINCIPAL, AGENT])
def test_complete_step_rejects_creator_side(monkeypatch, side_effects, relation_holder, user):
    """关键收紧：提单人 / 已确认跟进的被代理人不得替处理人推进阶段（403）。"""
    _wire_ticket(monkeypatch, _ticket(curr_step_agreed=True))
    relation_holder["relation"] = _relation("acknowledged")

    _expect_http_error(
        task_api.complete_task_step(
            task_id=TASK_ID, body=_complete_step_body(), db=_FakeSession(
                _step(11, 1, "现场排查"), _step(12, 2, "更换配件")
            ),
            current_user=user, request=None,
        ),
        403,
    )


def test_complete_step_allows_assignee(monkeypatch, side_effects, relation_holder):
    """接单人照常可推进阶段，回合归属记为 assigned。"""
    ticket = _wire_ticket(monkeypatch, _ticket(curr_step_agreed=True))
    relation_holder["relation"] = _relation("acknowledged")
    db = _FakeSession(_step(11, 1, "现场排查"), _step(12, 2, "更换配件"))

    result = asyncio.run(
        task_api.complete_task_step(
            task_id=TASK_ID, body=_complete_step_body(), db=db,
            current_user=ASSIGNEE, request=None,
        )
    )

    assert result is side_effects
    assert ticket.curr_step_id == 12
    assert ticket.curr_step_agreed is False
    assert ticket.step_last_updated_by == "assigned"


# ===========================================================================
# 需求 2-D：set-step-time / reopen-step 回合归属按真实操作人
# ===========================================================================
def test_set_step_time_records_assignee_side(monkeypatch, side_effects, relation_holder):
    """一锤定音：接单人操作记为 assigned 侧。"""
    ticket = _wire_ticket(monkeypatch, _ticket(escalate_count=1))
    relation_holder["relation"] = _relation("acknowledged")
    db = _FakeSession()

    asyncio.run(
        task_api.set_step_time(
            task_id=TASK_ID,
            body=SetStepTimeRequest(curr_step_endtime=datetime(2026, 9, 9, 10, 0, 0)),
            db=db, current_user=ASSIGNEE, request=None,
        )
    )

    assert ticket.curr_step_agreed is True
    assert ticket.step_last_updated_by == "assigned"
    assert db.commits == 1


def test_set_step_time_rejects_principal(monkeypatch, side_effects, relation_holder):
    """被代理人（即便已确认）不得一锤定音设置阶段时间。"""
    _wire_ticket(monkeypatch, _ticket(escalate_count=1))
    relation_holder["relation"] = _relation("acknowledged")

    _expect_http_error(
        task_api.set_step_time(
            task_id=TASK_ID,
            body=SetStepTimeRequest(curr_step_endtime=datetime(2026, 9, 9, 10, 0, 0)),
            db=_FakeSession(), current_user=PRINCIPAL, request=None,
        ),
        403,
    )


def test_reopen_step_records_creator_side_for_principal(monkeypatch, side_effects, relation_holder):
    """打回：被代理人（已确认）归 creator 侧，不再硬编码 'creator' 之外的臆测。"""
    ticket = _wire_ticket(
        monkeypatch,
        _ticket(status=TicketStatus.RESOLVED, resolved_at=datetime(2026, 9, 6, 10, 0, 0)),
    )
    relation_holder["relation"] = _relation("acknowledged")
    db = _FakeSession(_step(11, 1, "现场排查"))

    result = asyncio.run(
        task_api.reopen_step(
            task_id=TASK_ID,
            body=ReopenStepRequest(
                curr_step_id=11, curr_step_endtime=datetime(2026, 9, 10, 10, 0, 0)
            ),
            db=db, current_user=PRINCIPAL, request=None,
        )
    )

    assert result is side_effects
    assert ticket.status == TicketStatus.IN_PROGRESS
    assert ticket.curr_step_agreed is False
    assert ticket.step_last_updated_by == "creator"


def test_reopen_step_rejects_assignee(monkeypatch, side_effects, relation_holder):
    """打回是提单方特权：接单人 403。"""
    _wire_ticket(monkeypatch, _ticket(status=TicketStatus.RESOLVED))
    relation_holder["relation"] = _relation("acknowledged")

    _expect_http_error(
        task_api.reopen_step(
            task_id=TASK_ID,
            body=ReopenStepRequest(
                curr_step_id=11, curr_step_endtime=datetime(2026, 9, 10, 10, 0, 0)
            ),
            db=_FakeSession(_step(11, 1, "现场排查")), current_user=ASSIGNEE, request=None,
        ),
        403,
    )
