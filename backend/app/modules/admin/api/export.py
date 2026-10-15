"""admin 数据导出 API（承接 DAS export）。

MIGRATION.md 阶段 3：从 `app/modules/das/api/export.py` 搬迁而来，
路由前缀从 `/api/DAS/export` 迁移到 `/api/admin/export`。
"""
from fastapi import APIRouter, Depends, Query, HTTPException, Request, Body, Header
from typing import Any, Optional, Dict, List
from pydantic import BaseModel
from app.core.config import settings
from app.modules.admin.utils_das.config import security, DEBUG_MODE, AUTH_SERVICE_BASE_URL
from app.modules.admin.services.project_service import project_service
from app.modules.admin.services.permission_service import PermissionService
from app.modules.admin.utils_das.mqtt import publish_to_mqtt
from app.modules.admin.api.auth import (
    require_permission,
    has_permission_code,
)
from app.core.security import decode_token
from app.core.database import get_user_with_roles
from fastapi.responses import StreamingResponse
import gzip
import io
import json
from datetime import datetime
import requests

export_router = APIRouter(prefix="/export", tags=["admin-export"])

# 权限码：基于导出类型分别管控 license / 用户 / 完整授权导出与申请授权
PERM_LICENSE_EXPORT = "backend:project:license:export"
PERM_USER_EXPORT = "backend:project:user:export"
PERM_LICENSE_APPLY = "backend:project:license:apply"


def _verify_export_auth(
    request: Request,
    x_api_key: str = "",
    require_license: bool = True,
    require_users: bool = True,
) -> Optional[Dict[str, Any]]:
    """两种鉴权通道任选其一：
    1. X-API-Key 内部服务鉴权（配置 USP_PROJECT_SYNC_KEY）→ 跳过权限码
    2. JWT 用户鉴权 → 需通过对应 export 权限码

    返回 current_user（内部通道时为 None），鉴权失败直接抛 HTTPException。
    """
    internal_auth = bool(settings.USP_PROJECT_SYNC_KEY and x_api_key == settings.USP_PROJECT_SYNC_KEY)
    if internal_auth:
        return None

    # JWT 通道
    token = request.headers.get("Authorization", "")
    if token.startswith("Bearer "):
        token = token[7:]
    if not token:
        token = request.query_params.get("token", "")
    payload = decode_token(token) if token else None
    current_user = None
    if payload and payload.get("sub"):
        current_user = get_user_with_roles(payload["sub"])

    if not current_user:
        raise HTTPException(status_code=401, detail="未认证，请携带 JWT Token 或 X-API-Key")

    required_perms: List[str] = []
    if require_license:
        required_perms.append(PERM_LICENSE_EXPORT)
    if require_users:
        required_perms.append(PERM_USER_EXPORT)
    for perm in required_perms:
        if not has_permission_code(current_user, perm):
            raise HTTPException(status_code=403, detail=f"权限不足: 缺少 {perm}")

    return current_user


@export_router.post("/project/{project_code}", summary="导出项目数据")
async def export_project(
    request: Request,
    project_code: str,
    type: str = Query("all", description="导出类型: license, users, all；不传默认 all"),
    x_api_key: str = Header(default="", alias="X-API-Key"),
):
    if type not in ["license", "users", "all"]:
        raise HTTPException(status_code=400, detail="type参数必须是 'license', 'users' 或 'all'")

    _verify_export_auth(
        request, x_api_key,
        require_license=(type in ("license", "all")),
        require_users=(type in ("users", "all")),
    )

    export_data = {"project_code": project_code}

    # 导出授权包版本号（licence/人员变动自动 +1）
    project = project_service.get_project(project_code)
    export_data["version"] = str(project.get("export_version", 0) if project else 0)

    if type == "license" or type == "all":
        licenses = project_service.get_licenses_by_project_code(project_code, "all")
        if licenses:
            export_data["license_code"] = licenses[0].get("license_code", "")
        else:
            export_data["license_code"] = ""
    
    if type == "users" or type == "all":
        token = None
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        users = await PermissionService.get_project_uspinfo(request, token, project_code)
        export_data["user_list"] = users.get("user_list", [])
    
    json_data = json.dumps(export_data, ensure_ascii=False, indent=2).encode('utf-8')
    
    buffer = io.BytesIO()
    
    with gzip.GzipFile(fileobj=buffer, mode='w') as f:
        f.write(json_data)
    
    buffer.seek(0)
    
    filename = f"project_{project_code}_{type}_export.gz"
    
    return StreamingResponse(
        buffer,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f"attachment; filename={filename}"
        }
    )


@export_router.get("/project/{project_code}/meta", summary="查询项目导出授权包元信息")
async def export_project_meta(
    request: Request,
    project_code: str,
    x_api_key: str = Header(default="", alias="X-API-Key"),
) -> Dict[str, Any]:
    """轻量查询：只返回版本号和最近变更时间，用于项目侧判断是否需要重新拉取完整授权包。

    鉴权与 POST /project/{project_code} 完全相同。
    """
    _verify_export_auth(request, x_api_key, require_license=True, require_users=True)

    project = project_service.get_project(project_code)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")

    result: Dict[str, Any] = {"version": str(project.get("export_version", 0) or 0)}
    ts = project.get("export_version_updated_at")
    if ts:
        from datetime import datetime, timezone
        result["updated_at"] = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return result


@export_router.post("/apply_project_license", summary="申请项目授权")
async def apply_project_license(
    project_code: str = Body(..., description="项目代码"),
    mac: str = Body(..., description="MAC 地址"),
    start_date: str = Body(..., description="开始日期"),
    end_date: str = Body(..., description="结束日期"),
    max_vehicles: Optional[int] = Body(None, description="允许最大车数，为空表示不限制"),
    credentials: Optional = Depends(security),
    request: Request = None,
    current_user: Dict[str, Any] = require_permission(PERM_LICENSE_APPLY),
):
    # require_permission 已完成认证与权限校验，current_user 含 username/name 等字段
    user = current_user.get("username", "")
    user_name = current_user.get("name") or user

    # 仍调用 AUTH 服务获取最新 name（与原逻辑保持一致，失败则回退到 current_user.name）
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if user and token:
        try:
            url = f"{AUTH_SERVICE_BASE_URL}/users/{user}/detail"
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                user_data = response.json()
                user_name = user_data.get("name", user_name)
        except Exception:
            pass

    data = {
        "project_code": project_code,
        "mac": mac,
        "user": user,
        "start_date": start_date,
        "end_date": end_date,
        "max_vehicles": max_vehicles
    }

    status = publish_to_mqtt(data, wait_for_status=True, timeout=60)

    if status.get('status') == 'approved':
        license_data = {
            "project_code": status.get("project_code", project_code),
            "machine_code": mac,
            "apply_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "expire_time": end_date,
            "license_code": status.get("license_content", ""),
            "applicant": user_name,
            "applicant_id": user,
            "max_vehicles": max_vehicles
        }
        project_service.create_license(license_data)
    
    return status