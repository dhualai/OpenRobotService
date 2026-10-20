from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any, Optional
import uuid

from app.core.database import db_manager
from app.modules.admin.schemas.response import SuccessResponse, DataResponse
from app.modules.admin.api.auth import get_current_active_user_from_token, require_permission

router = APIRouter(prefix="/permissions", tags=["admin-permissions"])

def get_current_admin_user(current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)) -> Dict[str, Any]:
    if "admin" not in current_user.get('permissions', []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )
    return current_user


def is_project_member_or_admin(current_user: Dict[str, Any], project_id: Optional[str]) -> bool:
    """是不是「这个项目下的人」（或 admin 直通）。

    判据 = user_project_roles 里该项目下有任一角色，与「项目已关联人员」同一份数据
    （PermissionService.is_project_member）。全局角色不算——它们在库里 project_id 为空，
    代表平台级身份，不等于每个项目的成员。
    """
    if "admin" in current_user.get('permissions', []):
        return True
    if not project_id:
        return False
    from app.services.permission_service import PermissionService
    return PermissionService.is_project_member(current_user.get('id'), project_id)


def require_project_member(
    project_id: str,
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
) -> Dict[str, Any]:
    """项目信息树的结构类接口闸门：项目成员可写本项目的树与增补节点。

    project_id 由路径参数同名注入（路由里必须有 {project_id}）。节点级路由
    （/nodes/{node_id}）没有项目在路径上，走 info_nodes 里按节点反查项目的那个依赖。
    """
    if is_project_member_or_admin(current_user, project_id):
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="只有该项目下的人员可以编辑项目信息树",
    )

@router.get("/", response_model=DataResponse, summary="获取所有权限")
async def get_all_permissions(
    current_user: Dict[str, Any] = require_permission("backend:permission:base:read")
):
    try:
        from app.modules.admin.api.dispatch_dev import ensure_dispatch_dev_permission
        ensure_dispatch_dev_permission()
        from app.modules.admin.api.task_policy import ensure_task_policy_permission
        ensure_task_policy_permission()
        from app.modules.admin.services.knowledge_permissions import ensure_knowledge_contribution_permission
        ensure_knowledge_contribution_permission()
        # 公告管理：码在 permissions 表里存在才能在权限管理页勾给非超管角色。
        # 必须放在这里（读权限列表时），不能放接口函数体内，否则 403 会先挡住注册。
        from app.modules.admin.api.announcement import ensure_announcement_permission
        ensure_announcement_permission()
        permissions = db_manager.get_all_permissions()
        return DataResponse(
            code=0,
            message="success",
            data=permissions
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取权限列表失败: {str(e)}")

@router.get("/{permission_id}", response_model=DataResponse, summary="获取指定权限详情")
async def get_permission(
    permission_id: str,
    current_user: Dict[str, Any] = Depends(get_current_admin_user)
):
    try:
        permission = db_manager.get_permission(permission_id)
        if not permission:
            raise HTTPException(status_code=404, detail=f"权限不存在: {permission_id}")
        
        return DataResponse(
            code=0,
            message="success",
            data={"permission": permission}
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取权限详情失败: {str(e)}")

@router.post("/", response_model=SuccessResponse, summary="创建权限")
async def create_permission(
    permission_data: Dict[str, Any],
    current_user: Dict[str, Any] = require_permission("backend:permission:base:write")
):
    required_fields = ['code', 'name', 'resource_type', 'action']
    for field in required_fields:
        if field not in permission_data:
            raise HTTPException(status_code=400, detail=f"缺少必要参数: {field}")
    
    if permission_data.get('resource_type') != 'indicators':
        user_permissions = current_user.get('permissions', [])
        if "admin" not in user_permissions and "backend:permission:base:write" not in user_permissions:
            raise HTTPException(status_code=403, detail="没有权限创建权限")
    
    # try:
    permission_id = f"perm_{permission_data['code'].replace(':', '_')}"

    result = db_manager.add_permission(
        permission_id=permission_id,
        code=permission_data['code'],
        name=permission_data['name'],
        resource_type=permission_data['resource_type'],
        action=permission_data['action'],
        description=permission_data.get('description')
    )

    if result:
        return SuccessResponse(message="权限创建成功")
    else:
        raise HTTPException(status_code=400, detail="权限ID或编码已存在")
    # except HTTPException:
    #     raise
    # except Exception as e:
    #     raise HTTPException(status_code=500, detail=f"创建权限失败: {str(e)}")

@router.put("/{permission_id}", response_model=SuccessResponse, summary="更新权限")
async def update_permission(
    permission_id: str,
    permission_data: Dict[str, Any],
    current_user: Dict[str, Any] = require_permission("backend:permission:base:write")
):
    try:
        permission = db_manager.get_permission(permission_id)
        if not permission:
            raise HTTPException(status_code=404, detail=f"权限不存在: {permission_id}")
        
        if permission_data.get('resource_type') != 'indicators':
            user_permissions = current_user.get('permissions', [])
            if "admin" not in user_permissions and "backend:permission:base:write" not in user_permissions:
                raise HTTPException(status_code=403, detail="没有权限更新")

        if 'code' in permission_data and not str(permission_data['code']).strip():
            raise HTTPException(status_code=400, detail="权限编码不能为空")

        updatable_fields = ['code', 'name', 'resource_type', 'action', 'description', 'enabled']
        update_data = {}
        for field in updatable_fields:
            if field in permission_data:
                update_data[field] = permission_data[field]
        
        result = db_manager.update_permission(permission_id, **update_data)
        
        if result:
            return SuccessResponse(message="权限更新成功")
        else:
            raise HTTPException(status_code=500, detail="权限更新失败")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新权限失败: {str(e)}")

@router.delete("/{permission_id}", response_model=SuccessResponse, summary="删除权限")
async def delete_permission(
    permission_id: str,
    current_user: Dict[str, Any] = require_permission("backend:permission:base:delete")
):
    try:
        permission = db_manager.get_permission(permission_id)
        if not permission:
            raise HTTPException(status_code=404, detail=f"权限不存在: {permission_id}")
        
        if permission.get('resource_type') != 'indicators':
            user_permissions = current_user.get('permissions', [])
            if "admin" not in user_permissions and "backend:permission:base:delete" not in user_permissions:
                raise HTTPException(status_code=403, detail="没有权限删除权限")
        
        result = db_manager.delete_permission(permission_id)
        
        if result:
            return SuccessResponse(message="权限删除成功")
        else:
            raise HTTPException(status_code=500, detail="权限删除失败")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除权限失败: {str(e)}")
