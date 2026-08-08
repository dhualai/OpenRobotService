from fastapi import Request, HTTPException
from typing import Dict, Any, List, Optional
from app.core.database import db_manager
from app.services.identity_service import IdentityService
from app.services.user_service import user_service

class PermissionService:
    @staticmethod
    async def get_user_permissions(request: Request, token: str) -> Dict[str, Any]:
        try:
            permissions = db_manager.get_all_permissions()
            return {"permissions": permissions}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取权限失败: {str(e)}")

    @staticmethod
    async def get_projects(request: Request, token: str) -> Dict[str, Any]:
        try:
            from app.modules.admin.services.project_service import project_service
            projects = project_service.get_projects(0, 1000)
            return {"projects": projects}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取项目失败: {str(e)}")

    @staticmethod
    async def get_users_list(request: Request, token: str) -> List[Dict[str, Any]]:
        try:
            # db_manager 没有 get_all_users 方法，改用 user_service.get_user_list()
            # （已统一处理 external_credentials 的 JSON 解析与 null 兜底）
            # limit 用 999999999 以列出全部用户，与项目列表等接口的约定一致
            users = user_service.get_user_list(0, 999999999)
            result = []
            for user in users:
                # user_service 返回的 external_credentials 已经是 dict（null → {}）
                external_credentials = user.get('external_credentials') or {}
                result.append({
                    "id": user['id'],
                    "username": user['username'],
                    "name": user.get('name', user['username']),
                    "status": user.get('status', 'inactive'),
                    "external_credentials": external_credentials
                })
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取用户列表失败: {str(e)}")

    @staticmethod
    async def create_project(request: Request, token: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.modules.admin.services.project_service import project_service
            project = project_service.create_project(project_data)
            return {"project": project}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"创建项目失败: {str(e)}")

    @staticmethod
    async def assign_role(request: Request, token: str, username: str, role_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            user = db_manager.get_user(username)
            if not user:
                raise HTTPException(status_code=404, detail=f"用户不存在: {username}")
            
            project_id = role_data.get("project_id")
            role_ids = role_data.get("role_ids", [])
            
            for role_id in role_ids:
                user_project_role_id = f"upr_{user['id']}_{project_id}_{role_id}"
                IdentityService.add_user_project_role(
                    user_project_role_id,
                    user['id'],
                    project_id,
                    role_id
                )
            
            return {"success": True}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"分配角色失败: {str(e)}")

    @staticmethod
    async def remove_role(request: Request, token: str, username: str, role_data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            user = db_manager.get_user(username)
            if not user:
                raise HTTPException(status_code=404, detail=f"用户不存在: {username}")
            
            project_id = role_data.get("project_id")
            role_ids = role_data.get("role_ids", [])
            
            for role_id in role_ids:
                IdentityService.remove_user_project_role(
                    user['id'],
                    project_id,
                    role_id
                )
            
            return {"success": True}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"移除角色失败: {str(e)}")

    @staticmethod
    async def get_project_uspinfo(request: Request, token: str, project_code: str) -> Dict[str, List[Dict[str, str]]]:
        try:
            # 仅导出本项目已关联人员：通过 user_project_roles 表过滤，
            # project_code 与 Project.id 一致（见 delivery.py Project 定义），
            # 亦即 user_project_roles.project_id 列所存值。
            members = db_manager.get_project_members(project_code, include_usp=False)
            member_user_ids = {m.get('user_id') for m in members if m.get('user_id')}
            if not member_user_ids:
                return {"user_list": [], "user_key": []}

            # db_manager 没有 get_all_users 方法，改用 user_service.get_user_list()
            # （已统一处理 external_credentials 的 JSON 解析与 null 兜底）
            # limit 用 999999999 以列出全部用户，与项目列表等接口的约定一致
            users = user_service.get_user_list(0, 999999999)
            user_list = []
            user_key = []

            for user in users:
                # 仅保留本项目已关联人员
                if user.get('id') not in member_user_ids:
                    continue
                # user_service 返回的 external_credentials 已经是 dict（null → {}）
                external_credentials = user.get('external_credentials') or {}
                usp_info = external_credentials.get("usp", {})
                username = usp_info.get("username", "")
                if username and username not in user_key:
                    user_list.append({
                        "name": user.get("name", ""),
                        "username": username,
                        "password": usp_info.get("password", "")
                    })
                    user_key.append(username)

            return {"user_list": user_list, "user_key": user_key}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取USP信息失败: {str(e)}")