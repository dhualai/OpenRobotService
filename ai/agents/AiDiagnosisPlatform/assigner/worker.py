"""
派单后台 Worker — 定时扫描待派单工单并自动指派

设计原则：
  - 派单不需要 HTTP 接口，是纯内部后台任务
  - 每隔 N 秒扫描 tasks 表中未指派的工单，逐条派单
  - 单条派单失败不影响其他工单，不影响下一轮扫描
  - 派单结果直接写入数据库（assigned_to + metadata_info）
"""

import asyncio
from datetime import datetime
from typing import Optional

from ai.core.logging import get_logger
from ai.agents.AiDiagnosisPlatform.assigner import assign_ticket, load_engineers

logger = get_logger(__name__)


class AssignmentWorker:
    """后台派单 Worker

    使用方式:
        worker = AssignmentWorker(interval=30)
        task = asyncio.create_task(worker.run())
        # ... 服务运行中 ...
        await worker.stop()  # 优雅关闭
    """

    def __init__(self, interval: int = 60):
        """
        Args:
            interval: 扫描间隔（秒），默认 60
        """
        self.interval = interval
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def run(self):
        """主循环：定时扫描 → 逐条派单"""
        logger.info(f"派单 Worker 启动，扫描间隔={self.interval}s")

        # 启动时先检查工程师画像是否就绪
        engineers = load_engineers()
        if not engineers:
            logger.warning("工程师画像为空，派单 Worker 将跳过所有工单（等待 engineers.json 就绪）")
        else:
            logger.info(f"工程师画像已加载: {len(engineers)} 人")

        while not self._stop.is_set():
            try:
                await self._scan_and_assign()
            except Exception as e:
                logger.error(f"派单扫描轮次异常: {e}", exc_info=True)

            # 等待下一轮（支持提前退出）
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
                break  # stop 被设置，退出循环
            except asyncio.TimeoutError:
                pass  # 超时 = 正常，继续下一轮

        logger.info("派单 Worker 已停止")

    async def _scan_and_assign(self):
        """扫描待派单工单并逐条指派"""
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
        """从 MySQL 查询待指派工单（source='ai', status='pending', 未指派）"""
        try:
            from app.models.task import Task
            from app.core.db import SessionLocal

            db = SessionLocal()
            try:
                rows = (
                    db.query(Task)
                    .filter(
                        Task.source == "ai",
                        Task.status == "pending",
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
                        "priority": (r.priority.value if hasattr(r.priority, 'value') else str(r.priority or "中")),
                        "task_type": (r.task_type.value if hasattr(r.task_type, 'value') else str(r.task_type or "other")),
                        "session_id": (r.metadata_info or {}).get("session_id", "") if r.metadata_info else "",
                        "location": (r.metadata_info or {}).get("location", "") if r.metadata_info else "",
                        "robot_type": (r.metadata_info or {}).get("robot_type", "") if r.metadata_info else "",
                        "fault_code": (r.metadata_info or {}).get("fault_code", "") if r.metadata_info else "",
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

        result = await assign_ticket(
            ticket_id=str(t_id),
            title=ticket["title"],
            problem_description=ticket["description"],
            priority=ticket.get("priority", "中"),
            ticket_type=ticket.get("task_type", "other"),
            location=ticket.get("location", ""),
            robot_type=ticket.get("robot_type", ""),
            fault_code=ticket.get("fault_code", ""),
        )

        # 派单结果写回数据库
        self._update_task_assignee(t_id, result)

        # 同时从 Redis 待派单集合中移除
        try:
            from ai.core import get_memory_manager
            mgr = await get_memory_manager()
            session_id = ticket.get("session_id", "")
            if session_id:
                await mgr.remove_pending_ticket(session_id)
        except Exception:
            pass

        logger.info(
            f"派单完成: task_id={t_id}, assignee={result.engineer_name}, "
            f"confidence={result.confidence_score:.0%}, decision={result.decision_type}"
        )

    @staticmethod
    def _update_task_assignee(task_id: int, result) -> bool:
        """将派单结果写回 tasks 表"""
        try:
            from app.models.task import Task
            from app.core.db import SessionLocal

            db = SessionLocal()
            try:
                task = db.query(Task).filter(Task.id == task_id).first()
                if not task:
                    logger.warning(f"派单结果写回失败: task_id={task_id} 不存在")
                    return False

                task.assigned_to = result.engineer_id or result.engineer_name
                task.updated_at = datetime.utcnow()

                # 派单详情写入 metadata_info
                meta = task.metadata_info or {}
                meta["assignee_name"] = result.engineer_name
                meta["assignee_id"] = result.engineer_id
                meta["assign_confidence"] = result.confidence_score
                meta["assign_reasoning"] = result.reasoning
                meta["assign_decision_type"] = result.decision_type
                meta["assigned_at"] = datetime.utcnow().isoformat()
                task.metadata_info = meta

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
