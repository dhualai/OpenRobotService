from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, Any, List
import uuid

from app.core.database import db_manager, get_user_with_roles
from app.modules.aas.schemas.project import ProjectCreate, ProjectUpdate
from app.modules.aas.schemas.response import SuccessResponse, DataResponse
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

@router.get("/", response_model=DataResponse, summary="获取用户有读权限的项目列表")
async def get_projects(
    current_user: UserModel = require_permission("backend:project:base:read")
):
    all_projects = db_manager.get_all_projects()
    
    projects_detail = []
    for project in all_projects:
        projects_detail.append({
            "project_id": project['id'],
            "project_code": project.get('code', ''),
            "project_name": project['name'],
            "created_at": project.get('created_at', '')
        })
    
    return DataResponse(
        code=0,
        message="success",
        data={
            "total": len(projects_detail),
            "projects": projects_detail
        }
    )

@router.get("/me/", response_model=DataResponse, summary="获取用户自己的项目列表")
async def get_projects_me(
    current_user: UserModel = Depends(get_current_active_user_from_token)
):
    all_projects = db_manager.get_all_projects()
    
    user_projects = []
    for project in all_projects:
        if "admin" in current_user.roles:
            user_projects.append(project)
            continue
        for role_permissions in current_user.roles.values():
            if "admin" in role_permissions:
                user_projects.append(project)
                break
        
        if project['id'] in current_user.projectPermissions.keys():
            user_projects.append(project)
            continue

    projects_detail = []
    for project in user_projects:
        projects_detail.append({
            "project_id": project['id'],
            "project_code": project.get('code', ''),
            "project_name": project['name'],
            "created_at": project.get('created_at', '')
        })
    
    return DataResponse(
        code=0,
        message="success",
        data={
            "total": len(projects_detail),
            "projects": projects_detail
        }
    )

@router.get("/{username}/", response_model=DataResponse, summary="获取指定用户的项目列表")
async def get_user_projects(
    username: str,
    current_user: UserModel = require_permission("backend:project:base:read")
):
    target_user = get_user_with_roles(username)
    if not target_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )
    
    all_projects = db_manager.get_all_projects()
    
    user_projects = []
    for project in all_projects:
        if "admin" in target_user.roles:
            user_projects.append(project)
            continue
        for role_permissions in target_user.roles.values():
            if "admin" in role_permissions:
                user_projects.append(project)
                break
        
        if project['id'] in target_user.projectPermissions.keys():
            user_projects.append(project)
            continue
    
    projects_detail = []
    for project in user_projects:
        projects_detail.append({
            "project_id": project['id'],
            "project_code": project.get('code', ''),
            "project_name": project['name'],
            "created_at": project.get('created_at', '')
        })
    
    return DataResponse(
        code=0,
        message="success",
        data={
            "total": len(projects_detail),
            "projects": projects_detail
        }
    )

@router.get("/admin/all", response_model=DataResponse, summary="获取所有项目列表")
async def get_all_projects_admin(
    current_user: UserModel = Depends(get_current_admin_user)
):
    try:
        all_projects = db_manager.get_all_projects()
        
        projects_detail = []
        for project in all_projects:
            projects_detail.append({
                "project_id": project['id'],
                "project_code": project.get('code', ''),
                "project_name": project['name'],
                "created_at": project.get('created_at', '')
            })
        
        return DataResponse(
            code=0,
            message="success",
            data={
                "total": len(projects_detail),
                "projects": projects_detail
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取项目列表时出错: {str(e)}")

@router.get("/{project_id}", response_model=DataResponse, summary="获取指定项目详情")
async def get_project(
    project_id: str,
    current_user: UserModel = require_permission("backend:project:base:read")
):
    project = db_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_id}")
    
    return DataResponse(
        code=0,
        message="success",
        data={
            "project_id": project_id,
            "project_code": project.get('code', ''),
            "project_name": project['name']
        }
    )

@router.post("/", response_model=SuccessResponse, summary="创建新项目")
async def create_project(
    project_data: ProjectCreate,
    current_user: UserModel = require_permission("backend:project:base:write")
):
    project_code = project_data.code
    project_name = project_data.name or project_code
    project_id = project_code
    
    try:
        result = db_manager.add_project(project_id, project_code, project_name)
        if not result:
            raise HTTPException(status_code=400, detail="项目创建失败，可能已存在")
        
        return SuccessResponse(message=f"项目创建成功: {project_name}", 
                             data={"project_id": project_id, "project_code": project_code, "project_name": project_name})
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建项目时出错: {str(e)}")

@router.put("/{project_id}", response_model=SuccessResponse, summary="更新项目信息")
async def update_project(
    project_id: str,
    project_data: ProjectUpdate,
    current_user: UserModel = require_permission("backend:project:base:write")
):
    project = db_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_id}")
    
    try:
        result = db_manager.update_project(project_id, project_data.name)
        if not result:
            raise HTTPException(status_code=400, detail="项目更新失败")
        
        return SuccessResponse(message=f"项目更新成功: {project_data.name}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新项目时出错: {str(e)}")

@router.delete("/{project_id}", response_model=SuccessResponse, summary="删除项目")
async def delete_project(
    project_id: str,
    current_user: UserModel = require_permission("backend:project:base:delete")
):
    project = db_manager.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail=f"项目不存在: {project_id}")
    
    try:
        result = db_manager.delete_project(project_id)
        if not result:
            raise HTTPException(status_code=400, detail="项目删除失败")
        
        return SuccessResponse(message="项目删除成功")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除项目时出错: {str(e)}")

@router.get("/{project_id}/members", response_model=List[Dict[str, Any]])
async def get_project_members(
    project_id: str,
    current_user: UserModel = require_permission("backend:project:base:read")
):
    members = db_manager.get_project_members(project_id)
    
    return members

@router.get("/{project_id}/uspinfo", response_model=List[Dict[str, Any]])
async def get_project_uspinfo(
    project_id: str,
    current_user: UserModel = require_permission("backend:project:base:read")
):
    members = db_manager.get_project_members(project_id, True)
    
    return members