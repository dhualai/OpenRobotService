"""工单列表过滤排序：同主排序字段并列时必须有稳定 id 次键，避免 OFFSET 翻页重叠。

被测：TicketService._build_filter_query 生成的 ORDER BY。
不连库：只编译 SQL 断言末尾带 tasks.id DESC（调用方已按 id 排则不再重复垫）。
"""
import pytest
from sqlalchemy.dialects import mysql

from app.modules.tasks.schemas.ticket import TicketFilterRequest, TicketSort
from app.modules.tasks.services.ticket_service import TicketService


def _order_sql(query) -> str:
    compiled = query.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": False})
    text = str(compiled).lower()
    assert "order by" in text
    return text.split("order by", 1)[1]


@pytest.mark.asyncio
async def test_按_priority_排序时末尾垫_id_desc():
    req = TicketFilterRequest(sorts=[TicketSort(field="priority", direction="desc")])
    query = await TicketService._build_filter_query(req)
    order = _order_sql(query)
    assert "tasks.id" in order or "id desc" in order
    # id 必须在末尾（主排序之后）
    assert order.rstrip().endswith("id desc") or order.rstrip().endswith("`id` desc")


@pytest.mark.asyncio
async def test_默认_created_at_排序也垫_id_desc():
    req = TicketFilterRequest(sorts=[])
    query = await TicketService._build_filter_query(req)
    order = _order_sql(query)
    assert "created_at" in order
    assert order.rstrip().endswith("id desc") or order.rstrip().endswith("`id` desc")


@pytest.mark.asyncio
async def test_调用方已按_id_排序则不再重复垫():
    req = TicketFilterRequest(sorts=[TicketSort(field="id", direction="asc")])
    query = await TicketService._build_filter_query(req)
    order = _order_sql(query)
    # 只出现一次 id，方向保持调用方的 asc
    assert order.count("id") == 1
    assert "id asc" in order or "`id` asc" in order
