"""MySQL integration coverage for standard task query operations.

The suite is deliberately opt-in: it runs only when TEST_DATABASE_URL points to
an isolated MySQL database.  It never falls back to the development DATABASE_URL.

Query risk map (cross-ref with test IDs):
  R1  status comma-separated with invalid values silently skipped   -> TC-Q06
  R2  keyword ilike with no escaping                              -> TC-Q06
  R8  tag filter uses JSON contains([tag])                        -> TC-Q06

  Query flows covered:
    TC-Q01 - 无过滤全量分页查询
    TC-Q02 - 单字段精确过滤 (status, priority, type, created_by)
    TC-Q04 - 时间范围过滤 (created_at, deadline_at)
    TC-Q05 - 关键词模糊搜索
    TC-Q06 - 字符串运算符 (contains/notEquals + 多status逗号分隔)
    TC-Q08 - 标签过滤
    TC-Q09 - 空结果
    TC-Q11 - 不存在工单返回 None
    TC-Q12 - ID 0/-1 返回 None
    TC-Q13 - 复合过滤 AND 条件
    TC-Q14 - 复合过滤 OR 条件
    TC-Q15 - 复合过滤 嵌套 OR/AND
    TC-Q17 - is_null / not_null
    TC-Q18 - get_ticket_stats 统计
"""
import os
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.task import Task, TaskComment, TaskPriority, TaskStatus, TaskType
from app.modules.tasks.schemas.ticket import (
    TicketQueryParams, TicketFilterRequest, TicketFilter, TicketSort,
)
from app.modules.tasks.services.ticket_service import TicketService


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Set TEST_DATABASE_URL to an isolated MySQL database to run query persistence tests.",
)


def _async_url(url: str) -> str:
    if url.startswith("mysql+pymysql://"):
        return url.replace("mysql+pymysql://", "mysql+asyncmy://", 1)
    return url


_NOW = datetime(2026, 7, 21, 12, 0, 0)

SEED_TASKS = [
    Task(id=1001, title="机器人无法启动", description="启动后显示故障码 E1001",
         task_type=TaskType.PROBLEM, priority=TaskPriority.HIGH, status=TaskStatus.IN_PROGRESS,
         created_by="engineer-01", assigned_to="engineer-02", customer="customer-01",
         project_name="产线A项目", project_id="project-A",
         tags=["启动", "故障", "紧急"],
         created_at=_NOW - timedelta(days=5), updated_at=_NOW - timedelta(days=3),
         deadline_at=_NOW + timedelta(days=2), reply_count=3, view_count=10, source="manual"),
    Task(id=1002, title="导航精度偏差超过5cm", description="AGV行驶路线偏离超过5cm",
         task_type=TaskType.BUG, priority=TaskPriority.URGENT, status=TaskStatus.PENDING,
         created_by="engineer-01", assigned_to="engineer-03", customer="customer-02",
         project_name="产线B项目", project_id="project-B",
         tags=["导航", "精度"],
         created_at=_NOW - timedelta(days=3), updated_at=_NOW - timedelta(days=1),
         deadline_at=_NOW - timedelta(days=1), reply_count=5, view_count=25, source="manual"),
    Task(id=1003, title="新增自动充电功能", description="为AGV增加低电量自动回充功能",
         task_type=TaskType.FEATURE, priority=TaskPriority.MEDIUM, status=TaskStatus.NEW,
         created_by="customer-01", assigned_to=None, customer="customer-01",
         project_name="产线A项目", project_id="project-A",
         tags=["功能", "充电"],
         created_at=_NOW - timedelta(days=1), updated_at=_NOW - timedelta(days=1),
         deadline_at=_NOW + timedelta(days=7), reply_count=0, view_count=3,
         source="zentao", external_id="ZT-1001", external_url="http://zentao.example.com/story/1001"),
    Task(id=1004, title="机器人急停后无法恢复运行", description="急停按钮按下后解除，机器人无法自动恢复",
         task_type=TaskType.PROBLEM, priority=TaskPriority.HIGH, status=TaskStatus.RESOLVED,
         created_by="engineer-02", assigned_to="engineer-01", customer="customer-01",
         project_name="产线A项目", project_id="project-A",
         tags=["急停", "安全"],
         created_at=_NOW - timedelta(days=10), updated_at=_NOW - timedelta(days=2),
         resolved_at=_NOW - timedelta(days=2), deadline_at=_NOW - timedelta(days=3),
         reply_count=7, view_count=30, source="manual"),
    Task(id=1005, title="客户培训手册翻译", description="需要将操作手册翻译成英文",
         task_type=TaskType.SUPPORT, priority=TaskPriority.LOW, status=TaskStatus.CLOSED,
         created_by="customer-02", assigned_to="engineer-03", customer="customer-02",
         project_name="产线B项目", project_id="project-B",
         tags=["文档", "翻译"],
         created_at=_NOW - timedelta(days=15), updated_at=_NOW - timedelta(days=10),
         closed_at=_NOW - timedelta(days=10), deadline_at=_NOW - timedelta(days=8),
         reply_count=2, view_count=8, source="manual"),
]


@pytest.fixture(scope="module")
def mysql_schema():
    sync_engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.create_all(sync_engine, tables=[Task.__table__])
    sync_engine.execute(Task.__table__.delete().where(Task.id.in_([t.id for t in SEED_TASKS])))
    sync_engine.dispose()
    yield
    cleanup_engine = create_engine(TEST_DATABASE_URL)
    cleanup_engine.execute(Task.__table__.delete().where(Task.id.in_([t.id for t in SEED_TASKS])))
    cleanup_engine.dispose()


@pytest_asyncio.fixture
async def db_session(mysql_schema):
    engine = create_async_engine(_async_url(TEST_DATABASE_URL), pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        for t in SEED_TASKS:
            session.add(t)
        await session.commit()
        yield session
        await session.rollback()
        for t in SEED_TASKS:
            await session.delete(t)
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_tickets_returns_all_paged(db_session):
    """TC-Q01: no filters returns all tasks in descending created_at order."""
    params = TicketQueryParams(page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert result["total"] == 5
    assert result["page"] == 1
    assert len(result["items"]) == 5
    ids = [t.id for t in result["items"]]
    assert ids == [1003, 1002, 1001, 1004, 1005]


@pytest.mark.asyncio
async def test_get_tickets_respects_pagination(db_session):
    """TC-Q01: pagination correctly slices the result set."""
    params = TicketQueryParams(page=1, size=2)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 2
    assert result["total"] == 5
    assert result["pages"] == 3

    params2 = TicketQueryParams(page=3, size=2)
    result2 = await TicketService.get_tickets(db_session, params2, token=None)
    assert len(result2["items"]) == 1
    assert result2["page"] == 3


@pytest.mark.asyncio
async def test_get_tickets_filters_by_status_exact(db_session):
    """TC-Q02: exact status match."""
    params = TicketQueryParams(status="in_progress", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 1
    assert result["items"][0].id == 1001


@pytest.mark.asyncio
async def test_get_tickets_filters_by_priority_exact(db_session):
    """TC-Q02: exact priority match."""
    params = TicketQueryParams(priority=TaskPriority.HIGH, page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 2  # 1001, 1004


@pytest.mark.asyncio
async def test_get_tickets_filters_by_type_exact(db_session):
    """TC-Q02: exact ticket_type match."""
    params = TicketQueryParams(ticket_type=TaskType.PROBLEM, page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 2  # 1001, 1004


@pytest.mark.asyncio
async def test_get_tickets_filters_by_created_by(db_session):
    """TC-Q02: filter by created_by exact."""
    params = TicketQueryParams(created_by="engineer-01", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 2  # 1001, 1002


@pytest.mark.asyncio
async def test_get_tickets_filters_by_multiple_status(db_session):
    """TC-Q06 (R1): comma-separated status."""
    params = TicketQueryParams(status="in_progress,resolved", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 2


@pytest.mark.asyncio
async def test_get_tickets_title_contains_filter(db_session):
    """TC-Q06: title contains case-insensitive."""
    params = TicketQueryParams(title="机器人", title_op="contains", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 2  # 1001, 1004


@pytest.mark.asyncio
async def test_get_tickets_title_equals_filter(db_session):
    """TC-Q06: title equals exact."""
    params = TicketQueryParams(title="导航精度偏差超过5cm", title_op="equals", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 1
    assert result["items"][0].id == 1002


@pytest.mark.asyncio
async def test_get_tickets_title_not_equals_filter(db_session):
    """TC-Q06: title notEquals."""
    params = TicketQueryParams(title="机器人无法启动", title_op="notEquals", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 4
    assert all(t.id != 1001 for t in result["items"])


@pytest.mark.asyncio
async def test_get_tickets_filters_by_created_at_range(db_session):
    """TC-Q04: created_at range."""
    params = TicketQueryParams(
        created_at_start=_NOW - timedelta(days=6),
        created_at_end=_NOW - timedelta(days=2), page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 2  # 1001, 1002


@pytest.mark.asyncio
async def test_get_tickets_filters_by_deadline_at_range(db_session):
    """TC-Q04: deadline_at range."""
    params = TicketQueryParams(
        deadline_at_start=_NOW - timedelta(days=2),
        deadline_at_end=_NOW + timedelta(days=3), page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 1
    assert result["items"][0].id == 1001


@pytest.mark.asyncio
async def test_get_tickets_keyword_matches_title_and_description(db_session):
    """TC-Q05: keyword searches both title and description."""
    params = TicketQueryParams(keyword="故障", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 1
    assert result["items"][0].id == 1001


@pytest.mark.asyncio
async def test_get_tickets_keyword_no_matches(db_session):
    """TC-Q05: keyword no matches -> empty."""
    params = TicketQueryParams(keyword="不存在的关键词xyz123", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 0
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_get_tickets_filters_by_tag(db_session):
    """TC-Q08: tag filter."""
    params = TicketQueryParams(tag="导航", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 1
    assert result["items"][0].id == 1002


@pytest.mark.asyncio
async def test_get_tickets_tag_no_matches(db_session):
    """TC-Q08: tag no matches -> empty."""
    params = TicketQueryParams(tag="不存在的标签", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 0


@pytest.mark.asyncio
async def test_get_tickets_no_matching_records(db_session):
    """TC-Q09: incompatible filters -> empty."""
    params = TicketQueryParams(status="closed", priority=TaskPriority.HIGH, page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 0
    assert result["total"] == 0


@pytest.mark.asyncio
async def test_get_ticket_by_id_nonexistent_returns_none(db_session):
    """TC-Q11: non-existent id -> None."""
    ticket = await TicketService.get_ticket_by_id(db_session, 99999)
    assert ticket is None


@pytest.mark.asyncio
async def test_get_ticket_by_id_zero_returns_none(db_session):
    """TC-Q12: id=0 -> None."""
    ticket = await TicketService.get_ticket_by_id(db_session, 0)
    assert ticket is None


@pytest.mark.asyncio
async def test_get_ticket_by_id_negative_returns_none(db_session):
    """TC-Q12: negative id -> None."""
    ticket = await TicketService.get_ticket_by_id(db_session, -1)
    assert ticket is None


@pytest.mark.asyncio
async def test_filter_tickets_and_conditions(db_session):
    """TC-Q13: AND filter."""
    filter_req = TicketFilterRequest(
        filters=[TicketFilter(field="status", op="eq", value="in_progress"),
                 TicketFilter(field="priority", op="eq", value="high")],
        page=1, size=10)
    result = await TicketService.filter_tickets(db_session, filter_req, token=None)
    assert len(result["items"]) == 1
    assert result["items"][0].id == 1001


@pytest.mark.asyncio
async def test_filter_tickets_and_no_match(db_session):
    """TC-Q13: AND no match -> empty."""
    filter_req = TicketFilterRequest(
        filters=[TicketFilter(field="status", op="eq", value="new"),
                 TicketFilter(field="priority", op="eq", value="high")],
        page=1, size=10)
    result = await TicketService.filter_tickets(db_session, filter_req, token=None)
    assert len(result["items"]) == 0


@pytest.mark.asyncio
async def test_filter_tickets_or_conditions(db_session):
    """TC-Q14: OR filter."""
    filter_req = TicketFilterRequest(
        filters=[TicketFilter(or_conditions=[
            TicketFilter(field="status", op="eq", value="in_progress"),
            TicketFilter(field="status", op="eq", value="resolved")])],
        page=1, size=10)
    result = await TicketService.filter_tickets(db_session, filter_req, token=None)
    assert len(result["items"]) == 2


@pytest.mark.asyncio
async def test_filter_tickets_nested_and_or(db_session):
    """TC-Q15: nested AND/OR."""
    filter_req = TicketFilterRequest(
        filters=[TicketFilter(and_conditions=[
            TicketFilter(field="project_id", op="eq", value="project-A"),
            TicketFilter(or_conditions=[
                TicketFilter(field="status", op="eq", value="in_progress"),
                TicketFilter(field="status", op="eq", value="resolved")])])],
        page=1, size=10)
    result = await TicketService.filter_tickets(db_session, filter_req, token=None)
    assert len(result["items"]) == 2
    assert {t.id for t in result["items"]} == {1001, 1004}


@pytest.mark.asyncio
async def test_get_ticket_stats_counts_all_statuses(db_session):
    """TC-Q18: stats per status."""
    stats = await TicketService.get_ticket_stats(db_session)
    assert stats["total"] == 5
    assert stats["statistics"]["new"] == 1
    assert stats["statistics"]["in_progress"] == 1
    assert stats["statistics"]["pending"] == 1
    assert stats["statistics"]["resolved"] == 1
    assert stats["statistics"]["closed"] == 1


@pytest.mark.asyncio
async def test_filter_tickets_is_null_assigned_to(db_session):
    """TC-Q17: is_null."""
    filter_req = TicketFilterRequest(
        filters=[TicketFilter(field="assignedTo", op="is_null")], page=1, size=10)
    result = await TicketService.filter_tickets(db_session, filter_req, token=None)
    assert len(result["items"]) == 1
    assert result["items"][0].id == 1003


@pytest.mark.asyncio
async def test_filter_tickets_not_null_assigned_to(db_session):
    """TC-Q17: not_null."""
    filter_req = TicketFilterRequest(
        filters=[TicketFilter(field="assignedTo", op="not_null")], page=1, size=10)
    result = await TicketService.filter_tickets(db_session, filter_req, token=None)
    assert len(result["items"]) == 4
    assert all(t.assigned_to is not None for t in result["items"])


@pytest.mark.asyncio
async def test_get_tickets_id_op_gt(db_session):
    """TC-Q06: id_op=gt."""
    params = TicketQueryParams(id=1003, id_op="gt", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert all(t.id > 1003 for t in result["items"])


@pytest.mark.asyncio
async def test_get_tickets_id_op_lte(db_session):
    """TC-Q06: id_op=lte."""
    params = TicketQueryParams(id=1002, id_op="lte", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert all(t.id <= 1002 for t in result["items"])


@pytest.mark.asyncio
async def test_get_tickets_created_by_op_not_equals(db_session):
    """TC-Q06: created_by_op=notEquals."""
    params = TicketQueryParams(created_by="engineer-01", created_by_op="notEquals", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 3
    assert all(t.created_by != "engineer-01" for t in result["items"])


@pytest.mark.asyncio
async def test_get_tickets_filters_by_source(db_session):
    """TC-Q06: source filter."""
    params = TicketQueryParams(source="zentao", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 1
    assert result["items"][0].id == 1003


@pytest.mark.asyncio
async def test_get_tickets_filters_by_project_name(db_session):
    """TC-Q02: project_name filter."""
    params = TicketQueryParams(project_name="产线A项目", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 3


@pytest.mark.asyncio
async def test_get_tickets_status_invalid_skipped(db_session):
    """TC-Q03 (R1): invalid status silently skipped."""
    params = TicketQueryParams(status="in_progress,invalid_status,resolved", page=1, size=10)
    result = await TicketService.get_tickets(db_session, params, token=None)
    assert len(result["items"]) == 2
    assert {t.id for t in result["items"]} == {1001, 1004}


@pytest.mark.asyncio
async def test_get_ticket_by_id_increments_view_count(db_session):
    """TC-Q10 (R3): view_count increments when load_comments=False."""
    ticket = await TicketService.get_ticket_by_id(db_session, 1001)
    initial = ticket.view_count

    ticket = await TicketService.get_ticket_by_id(db_session, 1001)
    assert ticket.view_count == initial + 1

    ticket = await TicketService.get_ticket_by_id(db_session, 1001, load_comments=True)
    assert ticket.view_count == initial + 1
