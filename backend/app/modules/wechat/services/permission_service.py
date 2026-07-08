from fastapi import Request, HTTPException
import requests
from typing import Dict, Any, List, Optional
from app.core.config import settings


class PermissionService:
    @staticmethod
    async def get_user_list(request: Request, token: str) -> Dict[str, Any]:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            response = requests.get(
                f"{settings.AUTH_SERVICE_URL}/AAS/auth/users/",
                headers=headers,
                timeout=10
            )

            response.raise_for_status()

            result = response.json()

            return result

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