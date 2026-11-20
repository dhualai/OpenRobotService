"""代他人提单（代理提单）关系服务 —— 关系 CRUD、状态机与查询。

需求方案：`docs/PRODUCT/代他人提单（代理提单）功能设计方案.md` §3.2 / §3.3

职责边界：
- 关系写入（幂等 upsert）、状态流转（pending → acknowledged / declined）；
- 列表场景的**批量**读取（避免 N+1）；
- 「待我跟进」计数。

不含：权限判定（见 `app/core/ticket_roles.py`）与通知组装（见 `notification_utils.py`）。

安全：所有写操作以调用方传入的**已鉴权身份**为准，本层不接收前端传入的 principal_id 做鉴权。
"""
import logging
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.task_proxy_relation import (
    TaskProxyRelation,
    ProxyRelationStatus,
    ProxyRelationSource,
)
from app.core.user_identity import same_identity

logger = logging.getLogger(__name__)

# 拒绝原因长度上限（与 ORM remark VARCHAR(500) 对齐）
MAX_REMARK_LENGTH = 500


class ProxyRelationError(Exception):
    """代理关系业务错误（由 API 层转 HTTP 错误码）。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _now():
    """naive UTC（DB 会话已强制 UTC，见 db.py）。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ProxyRelationService:
    """代提关系服务。"""

    # ------------------------------------------------------------------ 写入

    @staticmethod
    async def create_relation(
        db: AsyncSession,
        *,
        task_id: int,
        agent_id: str,
        agent_username: Optional[str] = None,
        principal_id: str,
        principal_username: Optional[str] = None,
        source: str = ProxyRelationSource.MANUAL,
    ) -> Optional[TaskProxyRelation]:
        """建立代提关系（幂等）。

        同一 (task_id, agent_id, principal_id) 已存在时**不新建**，返回既有记录，
        避免重复代提产生脏数据（与 uk_task_agent_principal 唯一约束一致）。

        调用方需自行保证 principal_id 为合法在职用户（见 api 层校验）。
        """
        if not task_id or not agent_id or not principal_id:
            raise ProxyRelationError("代理关系缺少必要参数", 400)
        if same_identity(agent_id, principal_id):
            raise ProxyRelationError("不能为代自己提单", 400)

        existing = await ProxyRelationService.get_relation(
            db, task_id=task_id, agent_id=agent_id, principal_id=principal_id
        )
        if existing:
            logger.info(
                f"代理关系已存在，跳过创建 task_id={task_id} agent={agent_id} principal={principal_id}"
            )
            return existing

        relation = TaskProxyRelation(
            task_id=task_id,
            agent_id=agent_id,
            agent_username=agent_username,
            principal_id=principal_id,
            principal_username=principal_username,
            relation_status=ProxyRelationStatus.PENDING,
            source=source,
        )
        db.add(relation)
        await db.flush()
        logger.info(
            f"代理关系已建立 task_id={task_id} agent={agent_id} principal={principal_id} source={source}"
        )
        return relation

    @staticmethod
    async def mark_notified(db: AsyncSession, relation_id: int) -> None:
        """记录首次通知被代理人时间（best-effort，失败不影响主流程）。"""
        try:
            await db.execute(
                update(TaskProxyRelation)
                .where(
                    TaskProxyRelation.id == relation_id,
                    TaskProxyRelation.notified_at.is_(None),
                )
                .values(notified_at=_now())
            )
        except Exception as e:
            logger.warning(f"更新代提通知时间失败 relation_id={relation_id}: {e}")

    @staticmethod
    async def acknowledge(
        db: AsyncSession, relation: TaskProxyRelation, operator_username: str
    ) -> TaskProxyRelation:
        """被代理人确认跟进：pending → acknowledged。

        仅允许从 pending 流转；已是终态则报错（幂等性由前端按钮禁用保证，
        后端仍严格校验，避免重复请求把 declined 改成 acknowledged）。
        """
        if relation.relation_status != ProxyRelationStatus.PENDING:
            raise ProxyRelationError(
                f"当前关系状态为「{relation.relation_status}」，无法再次确认跟进", 409
            )
        relation.relation_status = ProxyRelationStatus.ACKNOWLEDGED
        relation.acked_at = _now()
        await db.flush()
        logger.info(
            f"被代理人确认跟进 task_id={relation.task_id} principal={relation.principal_id} "
            f"operator={operator_username}"
        )
        return relation

    @staticmethod
    async def decline(
        db: AsyncSession,
        relation: TaskProxyRelation,
        operator_username: str,
        remark: str,
    ) -> TaskProxyRelation:
        """被代理人拒绝（与我无关）：pending → declined，必须填原因。

        declined 为终态但**工单不中断**：代理人兜底推进，created_by 身份不受影响。
        """
        if relation.relation_status != ProxyRelationStatus.PENDING:
            raise ProxyRelationError(
                f"当前关系状态为「{relation.relation_status}」，无法再次拒绝", 409
            )
        reason = (remark or "").strip()
        if not reason:
            raise ProxyRelationError("请填写与本单无关的原因", 400)
        if len(reason) > MAX_REMARK_LENGTH:
            raise ProxyRelationError(f"原因长度不能超过 {MAX_REMARK_LENGTH} 字", 400)

        relation.relation_status = ProxyRelationStatus.DECLINED
        relation.remark = reason
        relation.declined_at = _now()
        await db.flush()
        logger.info(
            f"被代理人拒绝跟进 task_id={relation.task_id} principal={relation.principal_id} "
            f"operator={operator_username}"
        )
        return relation

    # ------------------------------------------------------------------ 查询

    @staticmethod
    async def get_relation(
        db: AsyncSession, *, task_id: int, agent_id: str, principal_id: str
    ) -> Optional[TaskProxyRelation]:
        """按三元组查关系（写入幂等用）。"""
        result = await db.execute(
            select(TaskProxyRelation).where(
                TaskProxyRelation.task_id == task_id,
                TaskProxyRelation.agent_id == agent_id,
                TaskProxyRelation.principal_id == principal_id,
            )
        )
        return result.scalars().first()

    @staticmethod
    async def get_by_id(db: AsyncSession, relation_id: int) -> Optional[TaskProxyRelation]:
        result = await db.execute(
            select(TaskProxyRelation).where(TaskProxyRelation.id == relation_id)
        )
        return result.scalars().first()

    @staticmethod
    async def get_task_relation(
        db: AsyncSession, task_id: int
    ) -> Optional[TaskProxyRelation]:
        """取工单的（唯一）生效关系；无关系返回 None。

        关系一对一（创建时确定、全程不变），故取首条即可；
        若历史脏数据有多条，优先返回 pending > acknowledged > declined。
        """
        result = await db.execute(
            select(TaskProxyRelation).where(TaskProxyRelation.task_id == task_id)
        )
        rows = result.scalars().all()
        if not rows:
            return None
        if len(rows) > 1:
            logger.warning(f"工单存在多条代理关系（应为 1 条）task_id={task_id} count={len(rows)}")
            order = {
                ProxyRelationStatus.PENDING: 0,
                ProxyRelationStatus.ACKNOWLEDGED: 1,
                ProxyRelationStatus.DECLINED: 2,
            }
            rows = sorted(rows, key=lambda r: order.get(r.relation_status, 9))
        return rows[0]

    @staticmethod
    async def get_relations_map(
        db: AsyncSession, task_ids: Iterable[int]
    ) -> Dict[int, TaskProxyRelation]:
        """批量取关系（列表场景避免 N+1）：单次 IN 查询后内存归并。"""
        ids = [i for i in set(task_ids or []) if i]
        if not ids:
            return {}
        result = await db.execute(
            select(TaskProxyRelation).where(TaskProxyRelation.task_id.in_(ids))
        )
        out: Dict[int, TaskProxyRelation] = {}
        for row in result.scalars().all():
            if row.task_id not in out:
                out[row.task_id] = row
        return out

    @staticmethod
    async def list_task_relations(db: AsyncSession, task_id: int) -> List[TaskProxyRelation]:
        """工单的全部关系记录（详情页展示，正常只有 1 条）。"""
        result = await db.execute(
            select(TaskProxyRelation)
            .where(TaskProxyRelation.task_id == task_id)
            .order_by(TaskProxyRelation.created_at.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def get_principal_task_ids(
        db: AsyncSession, principal_id: str, statuses: Optional[List[str]] = None
    ) -> List[int]:
        """查「我是被代理人」的工单 id 列表（供列表过滤拼 SQL 条件用）。

        principal_id 同时可能以 username 传入 → 由调用方先经 identity_keys 展开。
        """
        query = select(TaskProxyRelation.task_id).where(
            TaskProxyRelation.principal_id == principal_id
        )
        if statuses:
            query = query.where(TaskProxyRelation.relation_status.in_(statuses))
        result = await db.execute(query)
        return [row[0] for row in result.all() if row[0]]

    @staticmethod
    async def count_pending_for_principal(db: AsyncSession, principal_key: str) -> int:
        """「待我确认跟进」角标数：关系为 pending 且工单未终结。

        principal_key 需为 users.id（调用方归一）。
        """
        from app.modules.tasks.models.ticket import Ticket, TicketStatus

        result = await db.execute(
            select(func.count(TaskProxyRelation.id))
            .select_from(TaskProxyRelation)
            .join(Ticket, Ticket.id == TaskProxyRelation.task_id)
            .where(
                TaskProxyRelation.principal_id == principal_key,
                TaskProxyRelation.relation_status == ProxyRelationStatus.PENDING,
                Ticket.status.notin_([TicketStatus.CANCELED, TicketStatus.CLOSED]),
            )
        )
        return int(result.scalar() or 0)
