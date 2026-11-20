"""管理员端——工单关联规则配置 API。

允许管理员在线开关：
  - 前置工单是否阻塞 resolved / closed
  - 子工单是否阻塞 resolved / closed（默认关闭，弱关联）
  - 重复工单是否开启状态同步

鉴权：require_permission("frontend:admin:task-policy:manage")
"""
import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db as get_db
from app.modules.admin.api.auth import require_permission
from app.modules.tasks.services.task_policy_service import (
    get_all_policies,
    update_policies,
    get_policy_metadata,
)
from app.services.identity_service import IdentityService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/task-policy", tags=["admin-task-policy"])

PERM_CODE = "frontend:admin:task-policy:manage"


def ensure_task_policy_permission() -> None:
    """确保权限码 frontend:admin:task-policy:manage 已注册到 permissions 表。
    已存在则自动跳过（幂等），每次 API 调用都会触发一次。"""
    IdentityService.add_permission(
        permission_id="perm_frontend_admin_task_policy_manage",
        code=PERM_CODE,
        name="工单关联规则管理",
        resource_type="frontend",
        action="manage",
        description="后台「其他」中配置工单关联规则（前置/子工单阻塞、重复工单状态同步）",
    )


@router.get("", summary="获取当前工单关联策略")
async def get_policies(
    db: AsyncSession = Depends(get_db),
    _: Dict[str, Any] = require_permission(PERM_CODE),
):
    ensure_task_policy_permission()
    """返回当前生效的策略配置 + 元数据（description、默认值）。"""
    policies = await get_all_policies(db)
    metadata = get_policy_metadata()
    # 合并：给前端提供 key -> {value, default, description}
    merged = {}
    for key, meta in metadata.items():
        merged[key] = {
            "value": policies.get(key, meta["default"]),
            "default": meta["default"],
            "description": meta["description"],
        }
    return merged


@router.patch("", summary="批量更新工单关联策略")
async def patch_policies(
    updates: Dict[str, Any] = Body(..., description="key -> new_value (bool)"),
    db: AsyncSession = Depends(get_db),
    _: Dict[str, Any] = require_permission(PERM_CODE),
):
    ensure_task_policy_permission()
    """批量更新策略。只接受 DEFAULT_POLICIES 中定义的 key。

    请求体示例：
      {"block_predecessor_on_resolved": false, "duplicate_status_sync_enabled": true}
    """
    try:
        updated = await update_policies(db, updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("更新工单策略失败")
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")
    return {"message": "策略已更新", "policies": updated}
