"""admin 项目管理 API（承接 DAS projects）。

MIGRATION.md 阶段 3：从 `app/modules/das/api/projects.py` 搬迁而来，
路由前缀从 `/api/DAS/projects` 迁移到 `/api/admin/projects`。
"""
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from typing import Optional, Dict, List, Any
from app.modules.admin.schemas_das.request_models import ProjectCreate, ProjectUpdate, ProjectResponse
from app.modules.admin.services.project_service import project_service
from app.modules.admin.services.risk_service import risk_service
from app.modules.admin.services.transport_efficiency_service import transport_efficiency_service
from app.modules.admin.services.permission_service import PermissionService
from app.modules.admin.utils_das.config import security, DEBUG_MODE
from app.core.database import db_manager
from app.modules.admin.api.auth import require_permission
import logging

logger = logging.getLogger("admin")

project_router = APIRouter(prefix="/projects", tags=["admin-projects"])


@project_router.get("/", summary="获取项目列表")
async def get_projects(
    skip: int = Query(0, ge=0, description="跳过的项目数"),
    limit: int = Query(999999999, ge=1, le=999999999, description="返回的项目数"),
    keyword: Optional[str] = Query(None, description="搜索关键词"),
    status: Optional[str] = Query(None, description="按状态过滤"),
    execution_status: Optional[str] = Query(None, description="按执行状态过滤"),
    contact_person_id: Optional[str] = Query(None, description="按对接人ID过滤"),
    include_analysis: bool = Query(True, description="是否包含分析信息"),
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> List[ProjectResponse]:
    projects = []
    
    if keyword:
        projects = project_service.search_projects(keyword)
    elif status or execution_status or contact_person_id:
        projects = project_service.filter_projects(status, execution_status, contact_person_id)
    else:
        projects = project_service.get_projects(skip, limit)
    
    if not include_analysis:
        return projects
    
    project_codes = [project["project_code"] for project in projects]
    
    if project_codes:
        detailed_risks = risk_service.get_detailed_open_risks_by_project_codes(project_codes)
        
        for project in projects:
            project_code = project["project_code"]
            project_risks = detailed_risks.get(project_code, [])
            
            project["risks"] = 0
            
            custom_categories = {}
            for risk in project_risks:
                category = risk.get("custom_category") or "未分类"
                if category not in custom_categories:
                    custom_categories[category] = []
                custom_categories[category].append(risk)
            
            risk_summary = []
            
            task_execution_status = project_service.get_task_execution_status_7d(project_code)
            project["task_execution_status"] = task_execution_status
            project["task_execution_stats"] = project_service.get_task_execution_stats_7d(project_code)
            project["latest_manual_switch_count"] = transport_efficiency_service.get_latest_manual_switch_count(project_code)

            for category, risks in custom_categories.items():
                risk_summary.append(f"\n{category} ：{len(risks)}项")
                for risk in risks:
                    if risk["status"] == "关闭":
                        status_icon = "✅"
                    else:
                        status_icon = "❌"
                        project["risks"] += 1

                    risk_summary.append(f"- {risk['description']} - {risk.get('response_measure', '无')} {status_icon}")
            
            if risk_summary:
                project["project_summary"] = "\n".join(risk_summary)
                risk_list_summary = []
                for category, risks in custom_categories.items():
                    risk_list_summary.append(f"{category}: {len(risks)}项")
                project["risk_list"] = ", ".join(risk_list_summary)
            else:
                project["project_summary"] = "无风险"
                project["risk_list"] = "无"
    
    return projects


@project_router.get("/me", summary="获取当前用户关联项目列表")
async def get_my_projects(
    request: Request,
    include_analysis: bool = Query(True, description="是否包含分析信息"),
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> List[ProjectResponse]:
    from app.core.security import decode_token
    
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="无效的认证令牌")
    
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="令牌中缺少用户信息")
    
    from app.core.database import db_manager
    user = db_manager.get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    user_id = user.get("id")
    
    from app.services.permission_service import PermissionService
    user_roles = PermissionService.get_user_roles_all_projects(user_id)
    
    project_ids = [pid for pid in user_roles.keys() if pid != 'global']
    
    if not project_ids:
        return []
    
    projects = []
    for project_id in project_ids:
        project = project_service.get_project(project_id)
        if project:
            projects.append(project)
    
    if not include_analysis:
        return projects
    
    project_codes = [project["project_code"] for project in projects]
    
    if project_codes:
        detailed_risks = risk_service.get_detailed_open_risks_by_project_codes(project_codes)
        
        for project in projects:
            project_code = project["project_code"]
            project_risks = detailed_risks.get(project_code, [])
            
            project["risks"] = 0
            
            custom_categories = {}
            for risk in project_risks:
                category = risk.get("custom_category") or "未分类"
                if category not in custom_categories:
                    custom_categories[category] = []
                custom_categories[category].append(risk)
            
            risk_summary = []
            
            task_execution_status = project_service.get_task_execution_status_7d(project_code)
            project["task_execution_status"] = task_execution_status
            project["task_execution_stats"] = project_service.get_task_execution_stats_7d(project_code)
            project["latest_manual_switch_count"] = transport_efficiency_service.get_latest_manual_switch_count(project_code)

            for category, risks in custom_categories.items():
                risk_summary.append(f"\n{category} ：{len(risks)}项")
                for risk in risks:
                    if risk["status"] == "关闭":
                        status_icon = "✅"
                    else:
                        status_icon = "❌"
                        project["risks"] += 1

                    risk_summary.append(f"- {risk['description']} - {risk.get('response_measure', '无')} {status_icon}")
            
            if risk_summary:
                project["project_summary"] = "\n".join(risk_summary)
                risk_list_summary = []
                for category, risks in custom_categories.items():
                    risk_list_summary.append(f"{category}: {len(risks)}项")
                project["risk_list"] = ", ".join(risk_list_summary)
            else:
                project["project_summary"] = "无风险"
                project["risk_list"] = "无"
    
    return projects


@project_router.get("/{project_id}", summary="获取单个项目")
async def get_project(
    project_id: str,
    include_risks: bool = Query(False, description="是否包含风险信息"),
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> ProjectResponse:
    project = project_service.get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    if not include_risks:
        return project
    
    project_code = project["project_code"]
    detailed_risks = risk_service.get_detailed_open_risks_by_project_codes([project_code])
    project_risks = detailed_risks.get(project_code, [])
    
    project["risks"] = 0
    
    custom_categories = {}
    for risk in project_risks:
        category = risk.get("custom_category") or "未分类"
        if category not in custom_categories:
            custom_categories[category] = []
        custom_categories[category].append(risk)
    
    risk_summary = []
    
    for category, risks in custom_categories.items():
        risk_summary.append(f"\n{category} ：{len(risks)}项")
        for risk in risks:
            if risk["status"] == "已关闭":
                status_icon = "✅"
            else:
                status_icon = "❌"
                project["risks"] += 1

            risk_summary.append(f"- {risk['description']} - {risk.get('response_measure', '无')} {status_icon}")

    if risk_summary:
        project["project_summary"] = "\n".join(risk_summary)
        risk_list_summary = []
        for category, risks in custom_categories.items():
            risk_list_summary.append(f"{category}: {len(risks)}项")
        project["risk_list"] = ", ".join(risk_list_summary)
    else:
        project["project_summary"] = "无风险"
        project["risk_list"] = "无"
    
    return project


@project_router.get("/{project_id}/members", response_model=List[Dict[str, Any]], summary="获取项目已关联人员")
async def get_project_members(
    project_id: str,
    include_usp: bool = Query(False, description="是否包含外部凭证(usp)信息"),
    current_user: Dict[str, Any] = require_permission("backend:user:base:read")
):
    """返回指定项目下已关联的人员列表（含角色与汇报人信息）。

    底层调用 db_manager.get_project_members：按 user_project_roles 表中 project_id 过滤，
    关联 users 与 roles，逐行返回 role_name / username / name / project_id / role_id /
    report_to_name（include_usp=True 时额外返回 external_credentials）。
    一个用户在该项目下绑定多个角色时会出现多行。
    """
    try:
        members = db_manager.get_project_members(project_id, include_usp=include_usp)
        # get_project_members 的 report_to_name 子查询用 username 匹配 user_id 存在 bug 返回 null，
        # 此处直接查 user_project_roles.report_to_id 列覆盖，前端用 user_id == report_to_id 建树。
        report_to_map = db_manager.get_report_to_map(project_id)
        for m in members:
            m['report_to_id'] = report_to_map.get(m['user_id'])
            m.pop('report_to_name', None)
        return members
    except Exception as e:
        logger.error(f"获取项目已关联人员失败: project_id={project_id}, error={str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"获取项目已关联人员失败: {str(e)}")


@project_router.post("/", summary="创建项目")
async def create_project(
    request: Request,
    project_data: ProjectCreate,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> ProjectResponse:
    token = request.headers.get("Authorization", "")
    token = token[7:]

    # 项目编号/项目名称均为唯一 key：创建前比对库中已存在项目，命中即拒绝并提示用户重输。
    duplicate_msg = project_service.check_project_duplicate(project_data.project_code, project_data.name)
    if duplicate_msg:
        raise HTTPException(status_code=409, detail=f"{duplicate_msg}，请重新输入")

    if project_data.contact_person and project_data.contact_person_id:
        try:
            existing_projects = await PermissionService.get_projects(request, token)
            project_exists = any(p.get("project_code") == project_data.project_code for p in existing_projects["projects"])
            
            if not project_exists:
                project_dict = project_data.model_dump()
                await PermissionService.create_project(request, token, project_dict)
            
            role_data = {
                "project_id": project_data.project_code,
                "role_ids": ["project_contact"]
            }
            await PermissionService.assign_role(request, token, project_data.contact_person_id, role_data)
            
            from app.modules.admin.utils_das.security import decode_token
            from app.modules.admin.services.wechat_service import WeChatService
            
            current_user = decode_token(token)
            current_username = current_user.get("sub", "系统") if current_user else "系统"
            
            users = await PermissionService.get_users_list(request, token)
            contact_user = next((u for u in users if u["username"] == project_data.contact_person_id), None)
            if contact_user:
                current_user_info = next((u for u in users if u["username"] == current_username), None)
                current_user_name = current_user_info.get("name", current_username) if current_user_info else current_username
                title = project_data.name
                content = f"{current_user_name} 给您设置为项目 '{project_data.name}' 的对接人"
                WeChatService.send_notification(contact_user["id"], content, url='https://usp.ep-zl.com/wechat/projects')
        
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"权限服务操作失败: {str(e)}")
    
    try:
        project = project_service.create_project(project_data.model_dump())
    except IntegrityError:
        # 并发提交兜底：唯一约束（项目编号）冲突时同样按“项目已存在”处理
        raise HTTPException(status_code=409, detail="项目已存在，请重新输入")
    return project


@project_router.put("/{project_id}", summary="更新项目")
async def update_project(
    request: Request,
    project_id: str,
    update_data: ProjectUpdate,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> ProjectResponse:
    existing_project = project_service.get_project(project_id)
    if not existing_project:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    if update_data.contact_person_id and update_data.contact_person_id != existing_project["contact_person_id"]:
        token = request.headers.get("Authorization", "")
        token = token[7:]
        
        old_role_data = {
            "project_id": existing_project["project_code"],
            "role_ids": ["project_contact"]
        }
        await PermissionService.remove_role(request, token, existing_project["contact_person_id"], old_role_data)
        
        new_role_data = {
            "project_id": existing_project["project_code"],
            "role_ids": ["project_contact"]
        }
        await PermissionService.assign_role(request, token, update_data.contact_person_id, new_role_data)
        
        from app.modules.admin.utils_das.security import decode_token
        from app.modules.admin.services.wechat_service import WeChatService
        
        current_user = decode_token(token)
        current_username = current_user.get("sub", "系统") if current_user else "系统"
        
        users = await PermissionService.get_users_list(request, token)
        contact_user = next((u for u in users if u["username"] == update_data.contact_person_id), None)
        if contact_user:
            current_user_info = next((u for u in users if u["username"] == current_username), None)
            current_user_name = current_user_info.get("name", current_username) if current_user_info else current_username
            title = existing_project["name"]
            content = f"{current_user_name} 给您设置为项目 '{existing_project['name']}' 的对接人"
            WeChatService.send_notification(contact_user["id"], content, url='https://usp.ep-zl.com/wechat/projects')
    
    # 项目编号/项目名称是唯一 key：更新时若改动这两个字段，同样校验库中是否已被其他项目占用
    if update_data.project_code or update_data.name:
        new_code = update_data.project_code or existing_project["project_code"]
        new_name = update_data.name or existing_project["name"]
        duplicate_msg = project_service.check_project_duplicate(new_code, new_name, exclude_id=project_id)
        if duplicate_msg:
            raise HTTPException(status_code=409, detail=f"{duplicate_msg}，请重新输入")

    update_dict = {k: v for k, v in update_data.model_dump().items() if v is not None}

    project = project_service.update_project(project_id, update_dict)
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


@project_router.delete("/{project_id}", summary="删除项目")
async def delete_project(
    project_id: str,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> Dict[str, bool]:
    success = project_service.delete_project(project_id)
    if not success:
        raise HTTPException(status_code=404, detail="项目不存在")
    return {"success": True}


@project_router.post("/licenses", summary="创建项目授权")
async def create_project_license(
    request: Request,
    license_data: Dict,
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> Dict:
    required_fields = ["project_code", "apply_time", "expire_time", "license_code", "applicant", "applicant_id"]
    for field in required_fields:
        if field not in license_data:
            raise HTTPException(status_code=400, detail=f"缺少必要字段: {field}")
    
    license_item = project_service.create_license(license_data)
    return license_item


@project_router.get("/licenses/{project_code}", summary="获取项目授权信息")
async def get_project_licenses(
    project_code: str,
    request: Request,
    type: str = Query("last", description="获取类型: last - 最新授权, all - 所有授权"),
    credentials: Optional = Depends(security if not DEBUG_MODE else lambda: None)
) -> List[Dict]:
    logger.info(f"获取项目授权信息 - 项目代码: {project_code}, 类型: {type}")
    
    if type not in ["last", "all"]:
        logger.warning(f"无效的type参数: {type}")
        raise HTTPException(status_code=400, detail="type参数必须是 'last' 或 'all'")
    
    licenses = project_service.get_licenses_by_project_code(project_code, type)
    logger.info(f"从数据库获取到 {len(licenses)} 条授权信息")
    
    from app.core.database import get_user_with_roles

    # 将 applicant（DB 存的是 username）解析为用户真实姓名。
    # 直接查库，避免服务间 HTTP 调用（原走 AUTH_SERVICE_BASE_URL）失败时回退为 username。
    for license_item in licenses:
        applicant_username = license_item.get("applicant")
        if not applicant_username:
            continue
        try:
            applicant_user = get_user_with_roles(applicant_username)
            if applicant_user and applicant_user.get("name"):
                license_item["applicant"] = applicant_user["name"]
        except Exception as e:
            logger.warning(f"解析申请人姓名失败 - username: {applicant_username}, 错误: {str(e)}")

    logger.info(f"返回授权信息 - 项目代码: {project_code}, 授权数量: {len(licenses)}")
    return licenses