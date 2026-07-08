from fastapi import Request, HTTPException
import requests
from typing import Dict, Any, List, Optional
from app.modules.das.utils.config import AUTH_SERVICE_BASE_URL

class PermissionService:
    @staticmethod
    async def get_user_permissions(request: Request, token: str) -> Dict[str, Any]:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        try:
            response = requests.get(
                f"{AUTH_SERVICE_BASE_URL}/permissions",
                headers=headers,
                timeout=10
            )
            
            response.raise_for_status()
            
            result = response.json()
            
            return result.get("data", {})
            
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                raise HTTPException(status_code=401, detail="未授权，请提供有效的认证凭据")
            elif response.status_code == 403:
                raise HTTPException(status_code=403, detail="权限不足")
            else:
                raise HTTPException(status_code=response.status_code, detail=f"调用权限接口失败: {str(e)}")
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=f"调用权限接口失败: {str(e)}")
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=500, detail=f"解析权限数据失败: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取权限失败: {str(e)}")
    
    @staticmethod
    async def get_projects(request: Request, token: str) -> List[Dict[str, Any]]:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        try:
            response = requests.get(
                f"{AUTH_SERVICE_BASE_URL}/projects/",
                headers=headers,
                timeout=10
            )
            
            response.raise_for_status()
            
            result = response.json()
            
            return result.get("data", [])
            
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                raise HTTPException(status_code=401, detail="未授权，请提供有效的认证凭据")
            elif response.status_code == 403:
                raise HTTPException(status_code=403, detail="权限不足")
            else:
                raise HTTPException(status_code=response.status_code, detail=f"调用项目接口失败: {str(e)}")
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=f"调用项目接口失败: {str(e)}")
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=500, detail=f"解析项目数据失败: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取项目失败: {str(e)}")

    @staticmethod
    async def get_users_list(request: Request, token: str) -> List[Dict[str, Any]]:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            response = requests.get(
                f"{AUTH_SERVICE_BASE_URL}/users/",
                headers=headers,
                timeout=10
            )

            response.raise_for_status()

            result = response.json()

            users = result.get("data", []) if isinstance(result, dict) else result
            if not isinstance(users, list):
                users = []

            return users

        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                raise HTTPException(status_code=401, detail="未授权，请提供有效的认证凭据")
            elif response.status_code == 403:
                raise HTTPException(status_code=403, detail="权限不足")
            else:
                raise HTTPException(status_code=response.status_code, detail=f"调用用户列表接口失败: {str(e)}")
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=f"调用用户列表接口失败: {str(e)}")
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=500, detail=f"解析用户列表数据失败: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取用户列表失败: {str(e)}")

    @staticmethod
    async def create_project(request: Request, token: str, project_data: Dict[str, Any]) -> Dict[str, Any]:
        headers = {
            "Content-Type": "application/json"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        try:
            response = requests.post(
                f"{AUTH_SERVICE_BASE_URL}/projects/",
                headers=headers,
                json={"name": project_data.get("name"),"code": project_data.get("project_code")},
                timeout=10
            )
            
            response.raise_for_status()
            
            result = response.json()
            
            return result.get("data", {})
            
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                raise HTTPException(status_code=401, detail="未授权，请提供有效的认证凭据")
            elif response.status_code == 403:
                raise HTTPException(status_code=403, detail="权限不足")
            else:
                raise HTTPException(status_code=response.status_code, detail=f"调用项目接口失败: {str(e)}")
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=f"调用项目接口失败: {str(e)}")
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=500, detail=f"解析项目数据失败: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"创建项目失败: {str(e)}")
    
    @staticmethod
    async def assign_role(request: Request, token: str, username: str, role_data: Dict[str, Any]) -> Dict[str, Any]:
        headers = {
            "Content-Type": "application/json"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        try:
            response = requests.post(
                f"{AUTH_SERVICE_BASE_URL}/users/{username}/roles",
                headers=headers,
                json=role_data,
                timeout=10
            )
            
            response.raise_for_status()
            
            result = response.json()
            
            return result.get("data", {})
            
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                raise HTTPException(status_code=401, detail="未授权，请提供有效的认证凭据")
            elif response.status_code == 403:
                raise HTTPException(status_code=403, detail="权限不足")
            else:
                raise HTTPException(status_code=response.status_code, detail=f"调用角色分配接口失败: {response.text}")
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=f"调用角色分配接口失败: {str(e)}")
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=500, detail=f"解析角色分配数据失败: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"分配角色失败: {str(e)}")
    
    @staticmethod
    async def remove_role(request: Request, token: str, username: str, role_data: Dict[str, Any]) -> Dict[str, Any]:
        headers = {
            "Content-Type": "application/json"
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        try:
            response = requests.post(
                f"{AUTH_SERVICE_BASE_URL}/users/{username}/roles/remove",
                headers=headers,
                json=role_data,
                timeout=10
            )
            
            response.raise_for_status()
            
            result = response.json()
            
            return result.get("data", {})
            
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                raise HTTPException(status_code=401, detail="未授权，请提供有效的认证凭据")
            elif response.status_code == 403:
                raise HTTPException(status_code=403, detail="权限不足")
            else:
                raise HTTPException(status_code=response.status_code, detail=f"调用角色移除接口失败: {str(e)}")
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=f"调用角色移除接口失败: {str(e)}")
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=500, detail=f"解析角色移除数据失败: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"移除角色失败: {str(e)}")
    
    @staticmethod
    async def get_project_uspinfo(request: Request, token: str, project_code: str) -> Dict[str, List[Dict[str, str]]]:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        try:
            response = requests.get(
                f"{AUTH_SERVICE_BASE_URL}/projects/{project_code}/uspinfo",
                headers=headers,
                timeout=30
            )
            
            response.raise_for_status()
            
            result = response.json()
            
            user_list = []
            user_key =[]
            for user in result:
                usp_info = user.get("external_credentials", {}).get("usp", {})
                username = usp_info.get("username", "")
                if username in user_key:
                    continue
                user_list.append({
                    "name": user.get("name", ""),
                    "username": username,
                    "password": usp_info.get("password", "")
                })
                user_key.append(username)
            
            return {"user_list": user_list, "user_key": user_key}
        
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                raise HTTPException(status_code=401, detail="未授权，请提供有效的认证凭据")
            elif response.status_code == 403:
                raise HTTPException(status_code=403, detail="权限不足")
            else:
                raise HTTPException(status_code=response.status_code, detail=f"调用USP信息接口失败: {str(e)}")
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=500, detail=f"调用USP信息接口失败: {str(e)}")
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=500, detail=f"解析USP信息失败: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"获取USP信息失败: {str(e)}")