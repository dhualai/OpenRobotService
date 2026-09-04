"""
派单后台 Worker — Redis Pub/Sub 事件驱动 + 定时扫描兜底

设计原则：
  - Redis Pub/Sub 监听 "usp:new_ticket" 通道，新工单立即派单（事件驱动）
  - 每 N 秒定时扫描 MySQL 兜底，防止 Pub/Sub 丢消息或 Worker 重启期间的工单遗漏
  - 派单结果直接写入数据库（assigned_to + metadata_info）
"""

import asyncio
from typing import Optional

from ai.core.logging import get_logger
from ai.agents.AiDiagnosisPlatform.assigner import assign_ticket, load_engineers, ensure_dispatch_ready

logger = get_logger("ASSIGNER")

CHANNEL_NEW_TICKET = "usp:new_ticket"


class AssignmentWorker:
    """后台派单 Worker

    双通道：
      - 发布订阅：Redis SUBSCRIBE usp:new_ticket → 收到消息立即派单
      - 定时扫描：每 interval 秒扫 MySQL，捡漏（防 Pub/Sub 消息丢失）

    使用方式:
        worker = AssignmentWorker(interval=30)
        task = asyncio.create_task(worker.run())
        # ... 服务运行中 ...
        await worker.stop()  # 优雅关闭
    """

    def __init__(self, interval: int = 60):
        self.interval = interval
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._redis = None  # 用于 stop() 时主动断开连接，唤醒 listen()

    async def run(self):
        """启动：订阅通道 + 定时扫描 并行"""
        logger.info(f"派单 Worker 启动，扫描间隔={self.interval}s")

        engineers = load_engineers()
        if not engineers:
            logger.warning("工程师画像为空，派单 Worker 将跳过所有工单（等待 users 表人员数据就绪）")
        else:
            logger.info(f"工程师画像已加载: {len(engineers)} 人")

        # 预热派单流水线（首次加载配置 + 构建组件），避免重启后首单卡顿
        try:
            ensure_dispatch_ready()
        except Exception as e:
            logger.warning(f"派单流水线预热失败（将随首单懒加载）: {e}")

        # 两路并行：事件驱动 + 定时兜底
        await asyncio.gather(
            self._listen_pubsub(),
            self._poll_loop(),
        )

    async def _listen_pubsub(self):
        """订阅 Redis pub/sub 通道，收到新工单消息立即派单。

        Redis 断开/重启时自动重连（退化为仅轮询期间由定时扫描兜底）。
        """
        retry_interval = 5  # 重连间隔（秒）

        while not self._stop.is_set():
            try:
                import redis.asyncio as aioredis
                from ai.config import get_ai_config
                cfg = get_ai_config()
                self._redis = aioredis.from_url(cfg.redis_url or "redis://localhost:6379/0")
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(CHANNEL_NEW_TICKET)
                logger.info(f"派单 Worker 已订阅 Redis 通道: {CHANNEL_NEW_TICKET}")

                async for msg in pubsub.listen():
                    if self._stop.is_set():
                        break
                    if msg["type"] != "message":
                        continue
                    try:
                        task_id = int(msg["data"])
                    except (ValueError, TypeError):
                        logger.warning(f"派单 PubSub 收到无效 task_id: {msg['data']}")
                        continue

                    ticket = self._get_ticket_by_id(task_id)
                    if ticket is None:
                        logger.debug(f"派单 PubSub: task_id={task_id} 不存在或已指派，跳过")
                        continue
                    try:
                        await self._assign_one(ticket)
                    except Exception as e:
                        logger.error(f"派单 PubSub 失败: task_id={task_id}, error={e}", exc_info=True)

                await pubsub.unsubscribe(CHANNEL_NEW_TICKET)
                await self._redis.aclose()
                # 正常退出（收到停止信号）则退出重试循环
                break
            except Exception as e:
                logger.error(f"派单 PubSub 监听异常，{retry_interval}s 后自动重连: {e}", exc_info=True)
                if self._stop.is_set():
                    break
                try:
                    await asyncio.sleep(retry_interval)
                except Exception:
                    break

    async def _poll_loop(self):
        """定时扫描兜底：防 Pub/Sub 丢消息或重启期间遗漏"""
        while not self._stop.is_set():
            try:
                await self._scan_and_assign()
            except Exception as e:
                logger.error(f"派单扫描轮次异常: {e}", exc_info=True)

            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
                break
            except asyncio.TimeoutError:
                pass

        logger.info("派单 Worker 定时扫描已停止")

    @staticmethod
    def _get_ticket_by_id(task_id: int) -> Optional[dict]:
        """按 ID 查单条待派单工单（已被指派或不存在返回 None）"""
        try:
            from app.models.task import Task
            from app.core.db import SessionLocal
            db = SessionLocal()
            try:
                task = db.query(Task).filter(
                    Task.id == task_id,
                    Task.source == "ai",
                    Task.status == "new",
                ).first()
                if not task:
                    return None
                if task.assigned_to and task.assigned_to != "":
                    return None  # 已被其他途径指派，跳过
                return {
                    "id": task.id,
                    "title": task.title or "",
                    "description": task.description or "",
                    "created_by": task.created_by or "",
                    "priority": (task.priority.value if hasattr(task.priority, 'value') else str(task.priority or "中")),
                    "task_type": (task.task_type.value if hasattr(task.task_type, 'value') else str(task.task_type or "other")),
                    "session_id": (task.metadata_info or {}).get("session_id", "") if task.metadata_info else "",
                    "location": (task.metadata_info or {}).get("location", "") if task.metadata_info else "",
                    "robot_type": (task.metadata_info or {}).get("robot_type", "") if task.metadata_info else "",
                    "fault_code": (task.metadata_info or {}).get("fault_code", "") if task.metadata_info else "",
                    "preferred_assignee": (task.metadata_info or {}).get("preferred_assignee") if task.metadata_info else None,
                    "preferred_assignee_remark": (task.metadata_info or {}).get("preferred_assignee_remark") if task.metadata_info else None,
                    "diagnosis_hypotheses": (task.metadata_info or {}).get("diagnosis_hypotheses") if task.metadata_info else None,
                    "diagnosis_ruled_out": (task.metadata_info or {}).get("diagnosis_ruled_out") if task.metadata_info else None,
                    "diagnosis_collected_info": (task.metadata_info or {}).get("diagnosis_collected_info") if task.metadata_info else None,
                    "dispatch_hint": (task.metadata_info or {}).get("dispatch_hint", "") if task.metadata_info else "",
                    "project_name": task.project_name or "",
                    "project_id": task.project_id or "",
                }
            finally:
                db.close()
        except Exception as e:
            logger.error(f"按 ID 查工单失败: task_id={task_id}, error={e}", exc_info=True)
            return None

    async def _scan_and_assign(self):
        """扫描待派单工单并逐条指派"""
        # 每轮扫描前检查工程师画像缓存（10分钟 TTL，过期自动重拉）
        engineers = load_engineers()
        if not engineers:
            logger.debug("工程师画像为空，跳过本轮派单扫描")
            return

        tickets = self._get_pending_tickets()
        if not tickets:
            return

        logger.info(f"派单扫描: 发现 {len(tickets)} 条待派单工单")
        for t in tickets:
            if self._stop.is_set():
                break
            try:
                await self._assign_one(t)
            except Exception as e:
                logger.error(
                    f"派单失败: task_id={t['id']}, title={t.get('title', '')}, error={e}",
                    exc_info=True,
                )

    def _get_pending_tickets(self) -> list:
        """从 MySQL 查询待指派工单（source='ai', status='new', 未指派）"""
        try:
            from app.models.task import Task
            from app.core.db import SessionLocal

            db = SessionLocal()
            try:
                rows = (
                    db.query(Task)
                    .filter(
                        Task.source == "ai",
                        Task.status == "new",
                        (Task.assigned_to == None) | (Task.assigned_to == ""),
                    )
                    .order_by(Task.created_at.asc())
                    .all()
                )
                return [
                    {
                        "id": r.id,
                        "title": r.title or "",
                        "description": r.description or "",
                        "created_by": r.created_by or "",
                        "priority": (r.priority.value if hasattr(r.priority, 'value') else str(r.priority or "中")),
                        "task_type": (r.task_type.value if hasattr(r.task_type, 'value') else str(r.task_type or "other")),
                        "session_id": (r.metadata_info or {}).get("session_id", "") if r.metadata_info else "",
                        "location": (r.metadata_info or {}).get("location", "") if r.metadata_info else "",
                        "robot_type": (r.metadata_info or {}).get("robot_type", "") if r.metadata_info else "",
                        "fault_code": (r.metadata_info or {}).get("fault_code", "") if r.metadata_info else "",
                        "preferred_assignee": (r.metadata_info or {}).get("preferred_assignee") if r.metadata_info else None,
                        "preferred_assignee_remark": (r.metadata_info or {}).get("preferred_assignee_remark") if r.metadata_info else None,
                        "dispatch_hint": (r.metadata_info or {}).get("dispatch_hint", "") if r.metadata_info else "",
                        "project_name": r.project_name or "",
                        "project_id": r.project_id or "",
                    }
                    for r in rows
                ]
            finally:
                db.close()
        except Exception as e:
            logger.error(f"查询待派单工单失败: {e}", exc_info=True)
            return []

    async def _assign_one(self, ticket: dict):
        """派单一条工单并写回数据库"""
        t_id = ticket["id"]
        logger.debug(f"派单中: task_id={t_id}, title={ticket.get('title', '')[:30]}")

        # 原处理人 = 上一轮 task_dispatch_log（该工单已写过的最新一条）的 assigned_id。
        # 重派单流程：re_dispatch API 复位 assigned_to → 触发 worker 决策（此时本轮日志尚未写，
        # task_dispatch_log 最新一条仍是上一轮）→ 故"最新一条 assigned"即"用户重派前要换掉的原处理人"。
        # 首次派单（无任何日志）→ prev_assignee 为 None，不启用换人信号。
        prev_assignee = self._fetch_prev_assignee(t_id)

        result = await assign_ticket(
            ticket_id=str(t_id),
            title=ticket["title"],
            problem_description=ticket["description"],
            priority=ticket.get("priority", "中"),
            ticket_type=ticket.get("task_type", "other"),
            location=ticket.get("location", ""),
            robot_type=ticket.get("robot_type", ""),
            fault_code=ticket.get("fault_code", ""),
            project_name=ticket.get("project_name", ""),
            project_id=ticket.get("project_id", ""),
            creator=ticket.get("created_by", ""),
            preferred_assignee=ticket.get("preferred_assignee"),
            preferred_assignee_remark=ticket.get("preferred_assignee_remark"),
            prev_assignee=prev_assignee,
            diagnosis_hypotheses=ticket.get("diagnosis_hypotheses"),
            diagnosis_ruled_out=ticket.get("diagnosis_ruled_out"),
            diagnosis_collected_info=ticket.get("diagnosis_collected_info"),
            dispatch_hint=ticket.get("dispatch_hint") or None,
        )

        # 派单结果写回数据库
        ok = self._update_task_assignee(t_id, result)

        # 同时从 Redis 待派单集合中移除
        try:
            from ai.core import get_memory_manager
            mgr = await get_memory_manager()
            session_id = ticket.get("session_id", "")
            if session_id:
                await mgr.remove_pending_ticket(session_id)
        except Exception:
            pass

        # 新建工单通知：派单写回成功后，回调后端内部接口（仅传 task_id，
        # 后端按 task_id 查库组装标题/项目/截止时间/受理人后发通知）。
        # 失败仅告警，不影响派单主流程。
        if ok:
            try:
                import httpx
                from ai.config import get_ai_config
                cfg = get_ai_config()
                if cfg.backend_base_url and cfg.internal_api_key:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                        resp = await client.post(
                            f"{cfg.backend_base_url}/api/tasks/ticket-create-notification",
                            headers={"X-API-Key": cfg.internal_api_key},
                            json={"task_id": t_id},
                        )
                        if resp.status_code >= 300:
                            logger.warning(
                                f"新建工单通知回调非 2xx: task_id={t_id}, "
                                f"status={resp.status_code}, body={resp.text[:200]}"
                            )
            except Exception as e:
                logger.warning(f"新建工单通知发送失败 task_id={t_id}: {e}")

        logger.info(
            f"派单完成: task_id={t_id}, assignee={result.engineer_name}, "
            f"confidence={result.confidence_score:.0%}, decision={result.decision_type}"
        )

    @staticmethod
    def _fetch_prev_assignee(task_id: int) -> Optional[str]:
        """取重派单前一轮的原处理人 users.id（读 task_dispatch_log 已存在的最新一条 assigned_id）。

        首次派单（无任何日志）→ 返回 None；重派单时本轮日志尚未写入，
        task_dispatch_log 最新一条即"上一轮"，其 assigned_id 就是被换掉的原处理人。
        """
        try:
            from app.models.task_dispatch_log import TaskDispatchLog
            from app.core.db import SessionLocal
            from sqlalchemy import select
            db = SessionLocal()
            try:
                row = db.execute(
                    select(TaskDispatchLog.assigned_id)
                    .where(TaskDispatchLog.task_id == task_id)
                    .order_by(TaskDispatchLog.dispatch_round.desc())
                    .limit(1)
                ).scalar_one_or_none()
                return row if row else None
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"查询上一轮原处理人失败 task_id={task_id}: {e}")
            return None

    @staticmethod
    def _update_task_assignee(task_id: int, result) -> bool:
        """将派单结果写回 tasks 表"""
        try:
            from app.models.task import Task, TaskOperationLog, OperationType
            from app.models.task_dispatch_log import TaskDispatchLog
            from app.core.db import SessionLocal
            from sqlalchemy import func

            db = SessionLocal()
            try:
                task = db.query(Task).filter(Task.id == task_id).first()
                if not task:
                    logger.warning(f"派单结果写回失败: task_id={task_id} 不存在")
                    return False

                # engineer_id 已统一为 users.id（与 assigned_to 一致），无需反查
                # 注意：派单成功只写 assigned_to，不改状态——工单保持「新建」，
                # 由处理人「首次响应」（POST /{task_id}/respond）后才进入「处理中」。
                task.assigned_to = result.engineer_id or None
                task.updated_at = func.now()

                # ── 派单日志：统一落 task_dispatch_log（append-only，见需求方案 §4.2 §九-M1）。
                #    每轮派单（含首次）写一条；dispatch_round = 该工单已有最大轮次 + 1。
                #    与 tasks 更新、操作日志同一事务，保证强一致。 ──
                from sqlalchemy import select
                prev_round = db.scalar(
                    select(func.coalesce(func.max(TaskDispatchLog.dispatch_round), 0))
                    .where(TaskDispatchLog.task_id == task.id)
                ) or 0
                prof = dict(result.profile or {})
                db.add(TaskDispatchLog(
                    task_id=task.id,
                    dispatch_round=int(prev_round) + 1,
                    preferred_id=result.preferred_id,
                    assigned_id=result.engineer_id or "",
                    confidence=result.confidence_score,
                    decision_type=result.decision_type,
                    reasoning=result.reasoning,
                    profile=prof or None,
                    candidates=result.candidates or None,
                    matched_pref=result.matched_pref,
                    name_collision=result.name_collision,
                    pinyin_match=result.pinyin_match,
                ))

                # 派单操作日志：与 backend/app/modules/tasks/api/task.py 的 STATUS_LABEL
                # 中文风格对齐；operator 用 AI 系统标识，与 _log_task_creation 的
                # "system" 兜底风格一致。日志与派单写入同一事务，保证强一致：
                # 要么工单已派单+日志齐全，要么整体回滚由下次扫描重试派单。
                if task.assigned_to:
                    _AI_OP = "ai_dispatch"
                    _AI_OP_NAME = "AI 派单"
                    engineer_name = result.engineer_name or task.assigned_to or ""
                    # AI 派单记录：「工单已派单给 XXX」（不写状态变更日志：
                    # 派单不改状态，状态流转由处理人「首次响应」触发）
                    db.add(TaskOperationLog(
                        task_id=task.id,
                        operation_type=OperationType.AI_ASSIGN,
                        operator=_AI_OP,
                        operator_name=_AI_OP_NAME,
                        detail={
                            "new_assignee": result.engineer_id,
                            "assignee_name": engineer_name,
                            "confidence_score": result.confidence_score,
                            "decision_type": result.decision_type,
                            "reasoning": (result.reasoning or "")[:500],
                        },
                        description=f"工单已派单给 {engineer_name}",
                    ))

                db.commit()
                return True
            finally:
                db.close()
        except Exception as e:
            logger.error(f"派单结果写回数据库失败: task_id={task_id}, error={e}", exc_info=True)
            return False

    async def stop(self, timeout: float = 10.0):
        """优雅停止 Worker"""
        logger.info("派单 Worker 正在停止...")
        self._stop.set()
        # 断开 Redis 连接，唤醒 pubsub.listen()
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"派单 Worker 停止超时 ({timeout}s)")


def start_assignment_worker(interval: int = 60) -> tuple:
    """启动派单后台 Worker（便捷入口）

    Returns:
        (asyncio.Task, AssignmentWorker) — task 用于 await，worker 用于 stop()
    """
    worker = AssignmentWorker(interval=interval)
    task = asyncio.create_task(worker.run())
    worker._task = task
    return task, worker
