from fastapi import APIRouter, Depends, HTTPException, Request, status
from typing import List, Dict, Any
import uuid

from app.core.database import db_manager
from app.modules.aas.schemas.role import Role, RoleCreate, RolePermissionCreate
from app.modules.aas.schemas.response import SuccessResponse
from app.modules.aas.api.auth import get_current_active_user_from_token, require_permission
from app.core.models import User as UserModel

router = APIRouter()

def get_current_admin_user(current_user: UserModel = Depends(get_current_active_user_from_token)) -> UserModel:
    if "admin" not in current_user.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )
    return current_user

@router.get("/", response_model=List[Dict[str, str]], summary="获取所有角色列表")
async def get_roles(
    current_user: UserModel = require_permission("backend:role:base:read")
):
    try:
        roles = db_manager.get_all_roles()
        return roles
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取角色列表失败: {str(e)}")

@router.post("/", response_model=SuccessResponse, summary="创建新角色")
async def create_role(
    role_data: RoleCreate,
    current_user: UserModel = require_permission("backend:role:base:write")
):
    role_id = f"role_{uuid.uuid4().hex[:8]}"
    
    try:
        result = db_manager.add_role(role_id, role_data.name)
        if not result:
            raise HTTPException(status_code=400, detail="角色创建失败，可能已存在")
        
        return SuccessResponse(message=f"角色创建成功: {role_data.name}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建角色时出错: {str(e)}")

@router.get("/{role_id}/permissions", response_model=List[Dict[str, Any]], summary="获取角色权限详情")
async def get_role_all_permissions(
    role_id: str,
    current_user: UserModel = require_permission("backend:role:base:read")
):
    role = db_manager.get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    try:
        role_permissions = db_manager.get_role_permissions(role_id)
        
        permissions_details = []
        for rp in role_permissions:
            perm_detail = db_manager.get_permission(rp['permission_id'])
            if perm_detail:
                permissions_details.append(perm_detail)
        
        return permissions_details
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取角色权限失败: {str(e)}")

@router.post("/{role_id}/permissions", response_model=SuccessResponse, summary="给角色增加权限")
async def add_permissions_to_role(
    role_id: str,
    permission_data: Dict[str, Any],
    current_user: UserModel = require_permission("backend:role:permission:write")
):
    role = db_manager.get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    permission_ids = permission_data.get("permission_ids", [])
    
    if not permission_ids:
        raise HTTPException(status_code=400, detail="权限ID列表不能为空")
    
    for perm_id in permission_ids:
        permission = db_manager.get_permission(perm_id)
        if not permission:
            raise HTTPException(status_code=404, detail=f"权限 {perm_id} 不存在")
        
        if db_manager.check_role_permission(role_id, perm_id):
            raise HTTPException(status_code=400, detail=f"角色 {role_id} 已拥有权限 {perm_id}")
    
    try:
        added_count = 0
        for perm_id in permission_ids:
            role_permission_id = f"rp_{role_id}_{perm_id}_{uuid.uuid4().hex[:4]}"
            
            result = db_manager.add_role_permission(
                role_permission_id, role_id, perm_id
            )
            if result:
                added_count += 1
        
        return SuccessResponse(message=f"成功为角色添加{added_count}个权限")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"添加角色权限时出错: {str(e)}")

@router.get("/{role_id}/all-permissions", response_model=List[Dict[str, Any]], summary="获取角色所有权限详情")
async def get_role_all_permissions_2(
    role_id: str,
    current_user: UserModel = require_permission("backend:role:base:read")
):
    role = db_manager.get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    try:
        role_permissions = db_manager.get_role_permissions(role_id)
        
        permissions_details = []
        for rp in role_permissions:
            perm_detail = db_manager.get_permission(rp['permission_id'])
            if perm_detail:
                permissions_details.append(perm_detail)
        
        return permissions_details
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取角色权限失败: {str(e)}")

@router.delete("/{role_id}/permissions", response_model=SuccessResponse, summary="从角色中移除权限")
async def remove_permissions_from_role(
    role_id: str,
    permission_data: Dict[str, Any],
    current_user: UserModel = require_permission("backend:role:permission:write")
):
    role = db_manager.get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    permission_ids = permission_data.get("permission_ids", [])
    
    if not permission_ids:
        raise HTTPException(status_code=400, detail="权限ID列表不能为空")
    
    try:
        removed_count = 0
        for perm_id in permission_ids:
            result = db_manager.remove_role_permission(role_id, perm_id)
            if result:
                removed_count += 1
        
        return SuccessResponse(message=f"成功从角色中移除{removed_count}个权限")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"移除角色权限时出错: {str(e)}")

@router.post("/{role_id}/permissions/remove", response_model=SuccessResponse, summary="批量删除角色权限")
async def remove_permissions_from_role_post(
    role_id: str,
    permission_data: Dict[str, Any],
    current_user: UserModel = require_permission("backend:role:permission:write")
):
    role = db_manager.get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    permission_ids = permission_data.get("permission_ids", [])
    
    if not permission_ids:
        raise HTTPException(status_code=400, detail="权限ID列表不能为空")
    
    try:
        removed_count = 0
        for perm_id in permission_ids:
            result = db_manager.remove_role_permission(role_id, perm_id)
            if result:
                removed_count += 1
        
        return SuccessResponse(message=f"成功从角色中移除{removed_count}个权限")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"移除角色权限时出错: {str(e)}")

@router.delete("/{role_id}", response_model=SuccessResponse, summary="删除角色")
async def delete_role(
    role_id: str,
    current_user: UserModel = require_permission("backend:role:base:delete")
):
    role = db_manager.get_role(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="角色不存在")
    
    users_with_role = db_manager.get_users_by_role(role_id)
    if users_with_role and len(users_with_role) > 0:
        raise HTTPException(status_code=400, detail=f"无法删除角色，有{len(users_with_role)}个用户正在使用该角色")
    
    role_permissions = db_manager.get_role_permissions(role_id)
    
    try:
        if role_permissions:
            for rp in role_permissions:
                db_manager.remove_role_permission(role_id, rp['permission_id'])
        
        result = db_manager.delete_role(role_id)
        if not result:
            raise HTTPException(status_code=400, detail="角色删除失败")
        
        return SuccessResponse(message=f"角色 {role['name']} 删除成功")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除角色时出错: {str(e)}")