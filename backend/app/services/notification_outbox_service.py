"""通知排程（Outbox）服务。

把「命中即发」改为「按接收人工作时间排程发送」的核心：
- `schedule()`：业务侧调用，按接收人时区/工作日历算出 `scheduled_at` 后落库（幂等去重）
- `dispatch()`：投递单条到期记录（重查工单取最新数据）
- `cancel_task_notifications()`：工单完结/转派后取消尚未发出的提醒

时间约定：`scheduled_at` 一列存 **naive UTC**（与项目其余时间列一致）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core.database import AsyncSessionLocal, db_manager
from app.models.notification import NotificationOutbox
from app.services.working_time_service import WorkingTimeService

logger = logging.getLogger(__name__)

# 投递失败重试上限，超过则置 failed（不再自动重试）
MAX_ATTEMPTS = 3
# 失败后的退避间隔
RETRY_BACKOFF_MINUTES = 10


class NotificationOutboxService:
    """Outbox 读写与投递。"""

    # ── 排程写入 ────────────────────────────────────────
    @classmethod
    def schedule(
        cls,
        notify_type: int,
        recipient_id: str,
        task_id: Optional[int] = None,
        deadline_at: Optional[datetime] = None,
        payload: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        occurrence: Optional[str] = None,
    ) -> Dict[str, Any]:
        """为单个接收人排程一条通知。

        `occurrence` 用于区分同一工单同一类型的多次触发（如逾期第 0/1/2 天、
        临期的 24h 与 1h 两次预警），否则会被幂等键合并成一次。
        返回 `{"status": "scheduled"|"duplicate"|"skipped"|"error", "scheduled_at": ..., ...}`。

        `skipped`：临期预警已错过「截止前最后一个工作时段末尾」，不再补发，
        由截止后的逾期提醒接管（见 working_time_service.plan_send_time）。
        """
        if not recipient_id:
            return {"status": "error", "message": "缺少接收人"}

        if idempotency_key:
            key = idempotency_key
        elif occurrence:
            key = f"nt{notify_type}:t{task_id}:u{recipient_id}:o{occurrence}"
        else:
            key = f"nt{notify_type}:t{task_id}:u{recipient_id}"
        user = WorkingTimeService.get_user_context(recipient_id)
        planned = WorkingTimeService.plan_send_time(user, notify_type, deadline_at)
        if planned is None:
            return {
                "status": "skipped",
                "reason": "临期预警已过最后工作时段末尾，交由逾期提醒接管",
            }
        scheduled_at = WorkingTimeService.to_naive_utc(planned)

        # 截止时间一并落库：Worker 到期时需据此重新校验/重排
        payload = dict(payload or {})
        deadline_naive = WorkingTimeService.to_naive_utc(deadline_at)
        if deadline_naive:
            payload.setdefault("deadline_at", deadline_naive.isoformat())

        db = db_manager.get_db()
        try:
            exists = (
                db.query(NotificationOutbox.id, NotificationOutbox.scheduled_at)
                .filter(NotificationOutbox.idempotency_key == key)
                .first()
            )
            if exists:
                return {
                    "status": "duplicate",
                    "outbox_id": exists[0],
                    "scheduled_at": exists[1].isoformat() if exists[1] else None,
                }

            # 同一工单同一类型同一接收人，若已有一条待发记录且计划时刻完全相同，
            # 说明两次触发被顺延到了同一个工作时间节点 → 合并，不重复打扰（P1-9）
            merged = (
                db.query(NotificationOutbox.id)
                .filter(
                    NotificationOutbox.notify_type == notify_type,
                    NotificationOutbox.task_id == task_id,
                    NotificationOutbox.recipient_id == recipient_id,
                    NotificationOutbox.scheduled_at == scheduled_at,
                    NotificationOutbox.status.in_(["pending", "sending"]),
                )
                .first()
            )
            if merged:
                return {
                    "status": "duplicate",
                    "outbox_id": merged[0],
                    "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
                    "reason": "已存在同时刻待发通知，已合并",
                }

            record = NotificationOutbox(
                id=f"nob_{uuid.uuid4().hex}",
                notify_type=notify_type,
                task_id=task_id,
                recipient_id=recipient_id,
                scheduled_at=scheduled_at,
                status="pending",
                attempt_count=0,
                idempotency_key=key,
                payload=payload,
            )
            db.add(record)
            db.commit()
            return {
                "status": "scheduled",
                "outbox_id": record.id,
                "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
            }
        except Exception as e:
            db.rollback()
            logger.error(f"排程通知失败 type={notify_type} task={task_id} user={recipient_id}: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

    # ── 到期取件 ────────────────────────────────────────
    @classmethod
    def fetch_due(cls, limit: int = 100) -> List[Dict[str, Any]]:
        """取已到期且待发的记录（按计划时刻升序）。"""
        db = db_manager.get_db()
        try:
            now = datetime.utcnow()
            rows = (
                db.query(NotificationOutbox)
                .filter(
                    NotificationOutbox.status == "pending",
                    NotificationOutbox.scheduled_at <= now,
                )
                .order_by(NotificationOutbox.scheduled_at.asc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "notify_type": r.notify_type,
                    "task_id": r.task_id,
                    "recipient_id": r.recipient_id,
                    "scheduled_at": r.scheduled_at,
                    "attempt_count": r.attempt_count or 0,
                    "payload": r.payload,
                }
                for r in rows
            ]
        except Exception as e:
            logger.error(f"查询待发通知失败: {e}")
            return []
        finally:
            db.close()

    @classmethod
    def claim(cls, outbox_id: str) -> bool:
        """原子占位 pending → sending，避免重复投递。"""
        db = db_manager.get_db()
        try:
            affected = (
                db.query(NotificationOutbox)
                .filter(
                    NotificationOutbox.id == outbox_id,
                    NotificationOutbox.status == "pending",
                )
                .update({"status": "sending"})
            )
            db.commit()
            return affected > 0
        except Exception as e:
            db.rollback()
            logger.error(f"占位通知失败 id={outbox_id}: {e}")
            return False
        finally:
            db.close()

    @classmethod
    def mark_sent(cls, outbox_id: str) -> None:
        cls._update(outbox_id, {"status": "sent", "sent_at": datetime.utcnow()})

    @classmethod
    def mark_failed(cls, outbox_id: str, error: str) -> None:
        cls._update(
            outbox_id,
            {"status": "failed", "last_error": error[:2000] if error else None},
        )

    @classmethod
    def mark_cancelled(cls, outbox_id: str, reason: Optional[str] = None) -> None:
        """取消单条待发通知（如临期预警已错过有效时点）。"""
        cls._update(
            outbox_id,
            {
                "status": "cancelled",
                "last_error": (reason or "")[:2000] or None,
            },
        )

    @classmethod
    def reschedule(cls, outbox_id: str, when: datetime, error: Optional[str] = None) -> None:
        """重新排期（如到期时因临时节假日仍不可发）。"""
        cls._update(
            outbox_id,
            {
                "status": "pending",
                "scheduled_at": WorkingTimeService.to_naive_utc(when),
                "last_error": (error or "")[:2000] or None,
            },
        )

    @classmethod
    def _update(cls, outbox_id: str, values: Dict[str, Any]) -> None:
        db = db_manager.get_db()
        try:
            db.query(NotificationOutbox).filter(
                NotificationOutbox.id == outbox_id
            ).update(values)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"更新通知状态失败 id={outbox_id}: {e}")
        finally:
            db.close()

    @classmethod
    def cancel_task_notifications(cls, task_id: int) -> int:
        """取消某工单尚未发出的排程提醒（工单已完结/转派时调用）。"""
        if not task_id:
            return 0
        db = db_manager.get_db()
        try:
            affected = (
                db.query(NotificationOutbox)
                .filter(
                    NotificationOutbox.task_id == task_id,
                    NotificationOutbox.status.in_(["pending", "sending"]),
                )
                .update({"status": "cancelled"})
            )
            db.commit()
            return affected
        except Exception as e:
            db.rollback()
            logger.error(f"取消工单通知失败 task={task_id}: {e}")
            return 0
        finally:
            db.close()

    # ── 投递 ────────────────────────────────────────────
    @classmethod
    async def dispatch(cls, record: Dict[str, Any]) -> bool:
        """投递单条到期通知。重查工单，保证标题/截止时间/逾期天数为最新。"""
        # 延迟导入：避免与 tasks 模块形成启动期循环依赖
        from app.modules.tasks.services.ticket_service import TicketService
        from app.utils.notification_utils import NotificationUtils

        notify_type = record.get("notify_type")
        task_id = record.get("task_id")
        target_user = record.get("recipient_id")

        try:
            if not task_id:
                # 无关联工单（如个人统计类通知）：暂不投递，避免空模板
                logger.warning(f"通知 {record.get('id')} 缺少 task_id，跳过投递")
                return False

            async with AsyncSessionLocal() as db:
                ticket = await TicketService.get_ticket_by_id(db, task_id)
            if not ticket:
                logger.warning(f"工单 {task_id} 不存在，通知 {record.get('id')} 标记失败")
                cls.mark_failed(record["id"], "工单不存在")
                return False

            user_map = await TicketService._get_user_map(None)
            assigned_name = user_map.get(target_user, target_user)

            # 逾期天数：统一按 UTC 比较（修正原实现用本地 naive now 与 UTC deadline 比较的偏差）
            yuqi_day = ""
            deadline_utc = WorkingTimeService.to_utc(getattr(ticket, "deadline_at", None))
            if deadline_utc:
                now_utc = WorkingTimeService.now_utc()
                if now_utc > deadline_utc:
                    yuqi_day = f"{(now_utc - deadline_utc).total_seconds() / 86400:.0f}"

            await NotificationUtils.send_ticket_cuiban_notification(
                ticket_id=task_id,
                notify_type=notify_type,
                project_name=getattr(ticket, "project_name", "") or "",
                ticket_name=getattr(ticket, "title", "") or "",
                assigned_name=assigned_name,
                deadline_at=getattr(ticket, "deadline_at", None),
                create_at=getattr(ticket, "created_at", None),
                user_names=[target_user],
                token=None,
                yuqi_day=yuqi_day,
            )
            logger.info(
                f"[Outbox] 已投递 工单{task_id} type={notify_type} → {target_user} "
                f"(计划 {record.get('scheduled_at')})"
            )
            return True
        except Exception as e:
            logger.error(f"[Outbox] 投递失败 id={record.get('id')}: {e}")
            return False
