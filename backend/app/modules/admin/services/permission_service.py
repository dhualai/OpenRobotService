from fastapi import Request, HTTPException
from typing import Dict, Any, List, Optional
from app.core.database import db_manager
from app.services.identity_service import IdentityService

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
            users = db_manager.get_all_users()
            result = []
            for user in users:
                import json
                external_credentials = {}
                if user.get('external_credentials'):
                    try:
                        external_credentials = json.loads(user['external_credentials'])
                    except:
                        external_credentials = {}
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
            users = db_manager.get_all_users()
            user_list = []
            user_key = []
            
            for user in users:
                import json
                external_credentials = {}
                if user.get('external_credentials'):
                    try:
                        external_credentials = json.loads(user['external_credentials'])
                    except:
                        external_credentials = {}
                
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