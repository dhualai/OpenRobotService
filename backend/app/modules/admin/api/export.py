"""admin 数据导出 API（承接 DAS export）。

MIGRATION.md 阶段 3：从 `app/modules/das/api/export.py` 搬迁而来，
路由前缀从 `/api/DAS/export` 迁移到 `/api/admin/export`。
"""
from fastapi import APIRouter, Depends, Query, HTTPException, Request, Body
from typing import Optional, Dict, List
from pydantic import BaseModel
from app.modules.admin.utils_das.config import security, DEBUG_MODE, AUTH_SERVICE_BASE_URL
from app.modules.admin.services.project_service import project_service
from app.modules.admin.services.permission_service import PermissionService
from app.modules.admin.utils_das.mqtt import publish_to_mqtt
from app.core.security import decode_token
from fastapi.responses import StreamingResponse
import gzip
import io
import json
from datetime import datetime
import requests

export_router = APIRouter(prefix="/export", tags=["admin-export"])


@export_router.post("/project/{project_code}", summary="导出项目数据")
async def export_project(
    project_code: str,
    type: str = Query(..., description="导出类型: license, users, all"),
    credentials: Optional = Depends(security),
    request: Request = None
):
    if type not in ["license", "users", "all"]:
        raise HTTPException(status_code=400, detail="type参数必须是 'license', 'users' 或 'all'")
    
    export_data = {"project_code": project_code}
    
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


@export_router.post("/apply_project_license", summary="申请项目授权")
async def apply_project_license(
    project_code: str = Body(..., description="项目代码"),
    mac: str = Body(..., description="MAC 地址"),
    start_date: str = Body(..., description="开始日期"),
    end_date: str = Body(..., description="结束日期"),
    max_vehicles: Optional[int] = Body(None, description="允许最大车数，为空表示不限制"),
    credentials: Optional = Depends(security),
    request: Request = None
):
    user = ""
    user_name = ""
    token = None
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    payload = decode_token(token)
    if payload:
        user = payload.get("sub", "")

    if user and token:
        try:
            url = f"{AUTH_SERVICE_BASE_URL}/users/{user}/detail"
            headers = {"Authorization": f"Bearer {token}"}
            response = requests.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                user_data = response.json()
                user_name = user_data.get("name", user)
            else:
                user_name = user
        except Exception:
            user_name = user
    else:
        user_name = user

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