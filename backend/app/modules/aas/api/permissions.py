from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any
import uuid

from app.core.database import db_manager
from app.core.models import User as UserModel
from app.modules.aas.schemas.response import SuccessResponse, DataResponse
from app.modules.aas.api.auth import get_current_active_user_from_token, require_permission

router = APIRouter()

def get_current_admin_user(current_user: UserModel = Depends(get_current_active_user_from_token)) -> UserModel:
    if "admin" not in current_user.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )
    return current_user

@router.get("/", response_model=DataResponse, summary="获取所有权限")
async def get_all_permissions(
    current_user: UserModel = require_permission("backend:permission:base:read")
):
    try:
        permissions = db_manager.get_all_permissions()
        if "admin" in current_user.roles:
            return DataResponse(
                code=0,
                message="success",
                data={"permissions": permissions}
            )
        
        for role_permissions in current_user.roles.values():
            if "admin" in role_permissions:
                return DataResponse(
                    code=0,
                    message="success",
                    data={"permissions": permissions}
                )

        data_permissions = []
        for perm in permissions:
            if perm['resource_type'] == 'indicators':
                data_permissions.append(perm)
        permissions = data_permissions

        return DataResponse(
            code=0,
            message="success",
            data={"permissions": permissions}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取权限列表失败: {str(e)}")

@router.get("/{permission_id}", response_model=DataResponse, summary="获取指定权限详情")
async def get_permission(
    permission_id: str,
    current_user: UserModel = Depends(get_current_admin_user)
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
    current_user: UserModel = require_permission("backend:permission:base:write")
):
    required_fields = ['code', 'name', 'resource_type', 'action']
    for field in required_fields:
        if field not in permission_data:
            raise HTTPException(status_code=400, detail=f"缺少必要参数: {field}")
    
    be_create = False
    if "admin" in current_user.roles:
        be_create = True
    for role_permissions in current_user.roles.values():
        if "admin" in role_permissions:
            be_create = True
            break
    if permission_data['resource_type'] == 'indicators':
        be_create = True
    
    if not be_create:
        raise HTTPException(status_code=403, detail="没有权限创建权限")
    
    try:
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
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建权限失败: {str(e)}")

@router.put("/{permission_id}", response_model=SuccessResponse, summary="更新权限")
async def update_permission(
    permission_id: str,
    permission_data: Dict[str, Any],
    current_user: UserModel = require_permission("backend:permission:base:write")
):
    try:
        permission = db_manager.get_permission(permission_id)
        if not permission:
            raise HTTPException(status_code=404, detail=f"权限不存在: {permission_id}")
        
        be_create = False
        if "admin" in current_user.roles:
            be_create = True
        for role_permissions in current_user.roles.values():
            if "admin" in role_permissions:
                be_create = True
                break
        if permission_data['resource_type'] == 'indicators':
            be_create = True
        
        if not be_create:
            raise HTTPException(status_code=403, detail="没有权限更新")

        updatable_fields = ['name', 'action', 'description']
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
    current_user: UserModel = require_permission("backend:permission:base:delete")
):
    try:
        permission = db_manager.get_permission(permission_id)
        if not permission:
            raise HTTPException(status_code=404, detail=f"权限不存在: {permission_id}")
        
        be_create = False
        if "admin" in current_user.roles:
            be_create = True
        for role_permissions in current_user.roles.values():
            if "admin" in role_permissions:
                be_create = True
                break
        if permission['resource_type'] == 'indicators':
            be_create = True
        
        if not be_create:
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