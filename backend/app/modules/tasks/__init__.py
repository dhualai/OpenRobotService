"""tasks 模块（系统任务）——统一任务收件箱，处理方视角。

MIGRATION.md 阶段 3：承接 fqa/ticket 全部（工单/评论/状态机/派单/AI分配/催办），
与 call 模块共享同一 tasks 表。同时承接 fqa/tasks 异步任务管理。
"""
from fastapi import APIRouter
from app.modules.tasks.api.task import router as task_router
from app.modules.tasks.api.tasks import router as async_tasks_router
from app.modules.tasks.api.users import router as assignable_users_router
from app.modules.tasks.api.attachment import router as attachment_router
from app.modules.tasks.api.ws import router as ws_router

tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])

# assignable-users / files 须在 task_router 之前注册：避免 GET /tasks/assignable-users
# 或 /tasks/files/... 被 task_router 的 GET /tasks/{task_id}（贪婪路径参数）抢先匹配
tasks_router.include_router(assignable_users_router)
tasks_router.include_router(attachment_router)
tasks_router.include_router(task_router)
tasks_router.include_router(async_tasks_router)
tasks_router.include_router(ws_router)
