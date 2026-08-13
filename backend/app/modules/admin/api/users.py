from fastapi import APIRouter, Depends, HTTPException, Request, status, Query, Body
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
import uuid
import traceback
from sqlalchemy import text
from datetime import datetime

from typing import Dict, Any
from app.core.database import db_manager, get_user_with_roles, UserDB, get_async_db
from app.core.security import get_password_hash
from app.modules.admin.schemas.user import User, UserCreate, UserUpdate, UserDetail
from app.modules.admin.schemas.role import RoleBatchRemoval, RoleAssignment
from app.modules.admin.schemas.project import ProjectUserRoleAssignment
from app.modules.admin.schemas.response import SuccessResponse
from app.modules.admin.api.auth import get_current_active_user_from_token, require_permission
from app.services.hmac_utils import generate_password, chinese_to_pinyin
from app.models.task import Task, TaskType, TaskPriority
from app.models.identity import user_project_roles
from app.models.organization import Company, Department
from app.modules.tasks.schemas.ticket import TicketCreate
from app.modules.tasks.services.ticket_service import TicketService

router = APIRouter(prefix="/users", tags=["admin-users"])

def check_and_generate_unique_usp_name(db, base_usp_name, exclude_user_id=None):
    usp_name = base_usp_name
    suffix = 1
    
    while True:
        check_sql = """
        SELECT COUNT(*) 
        FROM users 
        WHERE JSON_EXTRACT(external_credentials, '$.usp.username') = :usp_name
        and id != :exclude_user_id
        """
        
        result = db.execute(text(check_sql), {"usp_name": usp_name, "exclude_user_id": exclude_user_id}).first()
        count = result[0] if result else 0
        
        if count == 0:
            return usp_name
        
        suffix += 1
        usp_name = f"{base_usp_name}{suffix}"

def get_current_admin_user(current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)) -> Dict[str, Any]:
    if "admin" not in current_user.get('permissions', []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )
    return current_user

@router.get("/", response_model=List[User], summary="获取用户列表")
async def get_users(
    skip: int = Query(0, ge=0, description="跳过的记录数"),
    limit: int = Query(100, ge=1, le=1000, description="返回的最大记录数"),
    current_user: Dict[str, Any] = require_permission("backend:user:base:read")
):
    db = db_manager.get_db()
    try:
        all_user_records = db.query(UserDB).all()
        paginated_user_records = all_user_records[skip:skip + limit]
        
        result = []
        user_ids = [user_record.id for user_record in paginated_user_records]
        all_users_roles = db_manager.get_all_users_roles_all_projects(user_ids)
        all_users_relations = db_manager.get_all_users_project_role_relations(user_ids)

        for user_record in paginated_user_records:
            user_roles = all_users_roles.get(user_record.id, {})

            import json
            external_credentials = {}
            if hasattr(user_record, 'external_credentials') and user_record.external_credentials:
                try:
                    external_credentials = json.loads(user_record.external_credentials)
                except:
                    external_credentials = {}

            user_response = User(
                id=user_record.id,
                username=user_record.username,
                permissions=[],
                roles=user_roles,
                name=getattr(user_record, 'name', None),
                status=getattr(user_record, 'status', 'inactive'),
                external_credentials=external_credentials,
                avatar_resource_id=getattr(user_record, 'avatar_resource_id', None),
                supervisor_id=getattr(user_record, 'supervisor_id', None),
                project_role_relations=all_users_relations.get(user_record.id, []),
            )

            result.append(user_response)
        
        return result
    except Exception as e:
        print(f"获取用户列表失败:{traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取用户列表失败: {str(e)}"
        )
    finally:
        db.close()

@router.get("/usp-username", summary="根据姓名生成去重的 USP 账户名")
async def generate_usp_username(
    name: str = Query(..., description="用户真实姓名"),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """将中文姓名转为拼音，与已有 USP 账户去重后返回合法账户名。"""
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="姓名不能为空")
    usp_username = chinese_to_pinyin(name)
    if not usp_username:
        raise HTTPException(status_code=400, detail="无法从姓名生成拼音")

    db = db_manager.get_db()
    try:
        existing_usernames = set()
        rows = db.query(UserDB.external_credentials).all()
        for (ec_json,) in rows:
            if not ec_json:
                continue
            try:
                import json
                ec = json.loads(ec_json)
                usp = ec.get("usp", {})
                if usp.get("username"):
                    existing_usernames.add(usp["username"])
            except Exception:
                continue

        # 排除当前用户已有的 USP 账户名（编辑自己资料时不视为冲突）
        # current_user 是 admin/api/auth 模块返回的 dict
        current_username = current_user.get("username", "") if isinstance(current_user, dict) else ""
        # 查当前用户已有的 USP 账户名
        current_usp = ""
        if current_username:
            current_db_user = db.query(UserDB).filter(UserDB.username == current_username).first()
            if current_db_user and current_db_user.external_credentials:
                try:
                    import json
                    ec = json.loads(current_db_user.external_credentials)
                    current_usp = ec.get("usp", {}).get("username", "")
                except Exception:
                    pass
        if current_usp and current_usp in existing_usernames:
            existing_usernames.discard(current_usp)

        if usp_username in existing_usernames:
            suffix = 2
            while f"{usp_username}{suffix}" in existing_usernames:
                suffix += 1
            usp_username = f"{usp_username}{suffix}"

        return {"usp_username": usp_username}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"生成 USP 账户名失败: {str(e)}"
        )
    finally:
        db.close()

@router.get("/options", summary="获取公司/部门可选项（主数据表，含审核状态）")
async def get_user_field_options(
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    """从 companies/departments 主数据表返回可选项。
    - approved 的全部可见
    - pending 的仅提交者本人可见
    - departments 按公司分组返回
    """
    db = db_manager.get_db()
    try:
        user_id = current_user.get('id', '')

        # 公司：approved 全部 + 本人 pending
        approved_companies = db.query(Company).filter(
            Company.status == 'approved'
        ).order_by(Company.name).all()
        my_pending_companies = db.query(Company).filter(
            Company.status == 'pending',
            Company.created_by == user_id,
        ).order_by(Company.name).all()

        companies_list = [
            {"id": c.id, "name": c.name, "status": c.status}
            for c in approved_companies + my_pending_companies
        ]

        # 部门：approved 全部 + 本人 pending，按公司分组
        all_company_ids = [c.id for c in approved_companies + my_pending_companies]
        approved_depts = db.query(Department).filter(
            Department.status == 'approved',
            Department.company_id.in_(all_company_ids) if all_company_ids else text('1=1'),
        ).all()
        my_pending_depts = db.query(Department).filter(
            Department.status == 'pending',
            Department.created_by == user_id,
        ).all()

        # 构建 company_id → name 映射
        company_name_map = {c.id: c.name for c in approved_companies + my_pending_companies}

        # 按公司名分组
        departments_by_company: Dict[str, List[Dict[str, Any]]] = {}
        my_pending_dept_list = []
        for d in approved_depts + my_pending_depts:
            comp_name = company_name_map.get(d.company_id, "未分类")
            if comp_name not in departments_by_company:
                departments_by_company[comp_name] = []
            departments_by_company[comp_name].append({
                "id": d.id,
                "name": d.name,
                "status": d.status,
            })
            if d.status == 'pending':
                my_pending_dept_list.append({
                    "id": d.id,
                    "name": d.name,
                    "company_name": comp_name,
                })

        return {
            "companies": companies_list,
            "departments_by_company": departments_by_company,
            "my_pending": {
                "companies": [{"id": c.id, "name": c.name} for c in my_pending_companies],
                "departments": my_pending_dept_list,
            },
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取可选项失败: {str(e)}"
        )
    finally:
        db.close()


# ===== 公司/部门提交与审核 =====

ADMIN_ASSIGNEE_ID = "user_admin"  # 审核工单指派给管理员


@router.post("/options/company", summary="提交新公司（创建 pending 记录 + 审核工单）")
async def submit_new_company(
    data: Dict[str, Any] = Body(...),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
    db: AsyncSession = Depends(get_async_db),
):
    name = (data.get('name') or '').strip()
    if not name:
        raise HTTPException(status_code=400, detail="公司名称不能为空")

    # 检查是否已存在（任意状态）
    sync_db = db_manager.get_db()
    try:
        existing = sync_db.query(Company).filter(Company.name == name).first()
        if existing:
            if existing.status == 'approved':
                raise HTTPException(status_code=400, detail="该公司已存在")
            elif existing.status == 'pending':
                raise HTTPException(status_code=400, detail="该公司已提交审核，请等待管理员处理")
            else:
                raise HTTPException(status_code=400, detail="该公司曾被驳回，请联系管理员")
    finally:
        sync_db.close()

    # 创建 pending 记录
    company_id = str(uuid.uuid4())
    user_id = current_user.get('id', '')
    user_name = current_user.get('name') or current_user.get('username', '')

    sync_db = db_manager.get_db()
    try:
        new_company = Company(
            id=company_id,
            name=name,
            status='pending',
            created_by=user_id,
        )
        sync_db.add(new_company)
        sync_db.commit()
    except Exception as e:
        sync_db.rollback()
        raise HTTPException(status_code=500, detail=f"创建公司记录失败: {str(e)}")
    finally:
        sync_db.close()

    # 创建审核工单
    ticket_data = TicketCreate(
        title=f"新公司录入审核：{name}",
        description=f"用户 {user_name} 申请新增公司「{name}」，请审核。",
        ticket_type=TaskType.OTHER,
        priority=TaskPriority.LOW,
        assigned_to=ADMIN_ASSIGNEE_ID,
        metadata_info={
            "approval_type": "new_company",
            "target_table": "companies",
            "target_id": company_id,
            "target_name": name,
            "submitted_by": user_id,
        },
    )
    try:
        ticket = await TicketService.create_ticket(db, ticket_data, user_id, {})
    except Exception as e:
        # 工单创建失败不回滚公司记录，管理员可在管理页面手动处理
        pass

    return {"company": {"id": company_id, "name": name, "status": "pending"}, "ticket_id": ticket.id if ticket else None}


@router.post("/options/department", summary="提交新部门（创建 pending 记录 + 审核工单）")
async def submit_new_department(
    data: Dict[str, Any] = Body(...),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
    db: AsyncSession = Depends(get_async_db),
):
    name = (data.get('name') or '').strip()
    company_id = (data.get('company_id') or '').strip()
    if not name:
        raise HTTPException(status_code=400, detail="部门名称不能为空")
    if not company_id:
        raise HTTPException(status_code=400, detail="请选择所属公司")

    # 检查公司是否存在且可见（approved 或本人 pending）
    sync_db = db_manager.get_db()
    try:
        company = sync_db.query(Company).filter(Company.id == company_id).first()
        if not company:
            raise HTTPException(status_code=404, detail="所选公司不存在")
        if company.status == 'rejected':
            raise HTTPException(status_code=400, detail="所选公司已被驳回")
        if company.status == 'pending' and company.created_by != current_user.get('id', ''):
            raise HTTPException(status_code=403, detail="所选公司正在审核中，无法添加部门")

        # 检查部门是否已存在
        existing = sync_db.query(Department).filter(
            Department.name == name,
            Department.company_id == company_id,
        ).first()
        if existing:
            if existing.status == 'approved':
                raise HTTPException(status_code=400, detail="该部门已存在")
            elif existing.status == 'pending':
                raise HTTPException(status_code=400, detail="该部门已提交审核，请等待管理员处理")
            else:
                raise HTTPException(status_code=400, detail="该部门曾被驳回，请联系管理员")
    finally:
        sync_db.close()

    # 创建 pending 记录
    dept_id = str(uuid.uuid4())
    user_id = current_user.get('id', '')
    user_name = current_user.get('name') or current_user.get('username', '')

    sync_db = db_manager.get_db()
    try:
        new_dept = Department(
            id=dept_id,
            name=name,
            company_id=company_id,
            status='pending',
            created_by=user_id,
        )
        sync_db.add(new_dept)
        sync_db.commit()
    except Exception as e:
        sync_db.rollback()
        raise HTTPException(status_code=500, detail=f"创建部门记录失败: {str(e)}")
    finally:
        sync_db.close()

    # 创建审核工单
    company_name = company.name if company else ""
    ticket_data = TicketCreate(
        title=f"新部门录入审核：{name}（{company_name}）",
        description=f"用户 {user_name} 申请新增部门「{name}」（所属公司：{company_name}），请审核。",
        ticket_type=TaskType.OTHER,
        priority=TaskPriority.LOW,
        assigned_to=ADMIN_ASSIGNEE_ID,
        metadata_info={
            "approval_type": "new_department",
            "target_table": "departments",
            "target_id": dept_id,
            "target_name": name,
            "company_id": company_id,
            "company_name": company_name,
            "submitted_by": user_id,
        },
    )
    try:
        ticket = await TicketService.create_ticket(db, ticket_data, user_id, {})
    except Exception as e:
        pass

    return {"department": {"id": dept_id, "name": name, "status": "pending"}, "ticket_id": ticket.id if ticket else None}


@router.put("/options/{target_type}/{target_id}/approve", summary="管理员审核通过公司/部门")
async def approve_option(
    target_type: str,
    target_id: str,
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    if target_type not in ('company', 'department'):
        raise HTTPException(status_code=400, detail="类型必须是 company 或 department")
    if current_user.get('id') != ADMIN_ASSIGNEE_ID:
        raise HTTPException(status_code=403, detail="无权限操作")

    sync_db = db_manager.get_db()
    try:
        if target_type == 'company':
            obj = sync_db.query(Company).filter(Company.id == target_id).first()
        else:
            obj = sync_db.query(Department).filter(Department.id == target_id).first()
        if not obj:
            raise HTTPException(status_code=404, detail="记录不存在")
        if obj.status == 'approved':
            raise HTTPException(status_code=400, detail="该记录已审核通过")

        obj.status = 'approved'
        obj.approved_by = current_user.get('id', '')
        obj.approved_at = datetime.now()
        obj.reject_reason = None
        sync_db.commit()

        return {"status": "approved", "id": target_id, "name": obj.name}
    except HTTPException:
        raise
    except Exception as e:
        sync_db.rollback()
        raise HTTPException(status_code=500, detail=f"审核操作失败: {str(e)}")
    finally:
        sync_db.close()


@router.put("/options/{target_type}/{target_id}/reject", summary="管理员驳回公司/部门")
async def reject_option(
    target_type: str,
    target_id: str,
    data: Dict[str, Any] = Body(...),
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token),
):
    if target_type not in ('company', 'department'):
        raise HTTPException(status_code=400, detail="类型必须是 company 或 department")
    if current_user.get('id') != ADMIN_ASSIGNEE_ID:
        raise HTTPException(status_code=403, detail="无权限操作")

    reason = (data.get('reason') or '').strip()

    sync_db = db_manager.get_db()
    try:
        if target_type == 'company':
            obj = sync_db.query(Company).filter(Company.id == target_id).first()
        else:
            obj = sync_db.query(Department).filter(Department.id == target_id).first()
        if not obj:
            raise HTTPException(status_code=404, detail="记录不存在")
        if obj.status == 'rejected':
            raise HTTPException(status_code=400, detail="该记录已被驳回")

        obj.status = 'rejected'
        obj.approved_by = current_user.get('id', '')
        obj.approved_at = datetime.now()
        obj.reject_reason = reason or None
        sync_db.commit()

        return {"status": "rejected", "id": target_id, "name": obj.name, "reason": reason}
    except HTTPException:
        raise
    except Exception as e:
        sync_db.rollback()
        raise HTTPException(status_code=500, detail=f"驳回操作失败: {str(e)}")
    finally:
        sync_db.close()

@router.post("/", response_model=User)
async def create_user(
    user_data: UserCreate,
    current_user: Dict[str, Any] = require_permission("backend:user:base:write")
):
    if db_manager.get_user(user_data.username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在"
        )
    
    user_id = f"user_{uuid.uuid4().hex[:8]}"
    
    hashed_password = get_password_hash(user_data.password)
    success = db_manager.add_user(
        user_id=user_id,
        username=user_data.username,
        hashed_password=hashed_password,
        permissions=user_data.permissions,
        name=user_data.name,
        status=user_data.status,
        external_credentials=user_data.external_credentials,
        company=user_data.company,
        department=user_data.department,
        responsibility_modules=user_data.responsibility_modules,
        job_level=user_data.job_level,
        duty_text=user_data.duty_text,
        supervisor_id=user_data.supervisor_id,
    )
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="创建用户失败"
        )
    
    default_role_id = "user"
    user_project_role_id = f"upr_{user_id}_global_{default_role_id}"
    db_manager.add_user_project_role(
        user_project_role_id,
        user_id,
        None,
        default_role_id
    )
    
    created_user = get_user_with_roles(user_data.username)
    return User(
        id=created_user['id'],
        username=created_user['username'],
        permissions=created_user['permissions'],
        roles=created_user['roles'],
        projectPermissions=created_user.get('projectPermissions', {}),
        name=created_user.get('name'),
        status=created_user.get('status', 'inactive'),
        external_credentials=_mask_usp_password(created_user.get('external_credentials', {})),
        avatar_resource_id=created_user.get('avatar_resource_id'),
        company=created_user.get('company'),
        department=created_user.get('department'),
        supervisor_id=created_user.get('supervisor_id'),
    )

def _mask_usp_password(external_credentials: Dict) -> Dict:
    """屏蔽 USP 密码哈希：已设置密码时返回 "-" 作为哨兵，未设置时保持为空"""
    if not external_credentials:
        return external_credentials
    usp = external_credentials.get('usp', {})
    if usp and usp.get('password'):
        import copy
        masked = copy.deepcopy(external_credentials)
        masked['usp']['password'] = '-'
        return masked
    return external_credentials


@router.get("/{username}/detail", response_model=UserDetail, summary="获取用户详细信息")
async def get_user_detail(
    username: str,
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    user = get_user_with_roles(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )
    
    return UserDetail(
        id=user['id'],
        username=user['username'],
        permissions=user['permissions'],
        roles=user['roles'],
        projectPermissions=user.get('projectPermissions', {}),
        name=user.get('name'),
        status=user.get('status', 'inactive'),
        external_credentials=_mask_usp_password(user.get('external_credentials', {})),
        avatar_resource_id=user.get('avatar_resource_id'),
        company=user.get('company'),
        department=user.get('department'),
        responsibility_modules=user.get('responsibility_modules', {}),
        job_level=user.get('job_level', 1),
        duty_text=user.get('duty_text'),
        supervisor_id=user.get('supervisor_id'),
    )

@router.put("/{username}", response_model=User)
async def update_user(
    username: str,
    user_data: UserUpdate,
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    if current_user['username'] != username and "admin" not in current_user.get('permissions', []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="没有权限更新该用户信息"
        )

    user = db_manager.get_user(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )
    
    update_data = {}
    if user_data.password:
        update_data["password_hash"] = get_password_hash(user_data.password)
    if user_data.permissions is not None:
        if "admin" not in current_user.get('permissions', []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="没有权限修改权限"
            )
        update_data["permissions"] = user_data.permissions
    if user_data.name is not None:
        update_data["name"] = user_data.name
    if user_data.status is not None:
        update_data["status"] = user_data.status
    if user_data.external_credentials is not None:
        external_creds = user_data.external_credentials
        if "usp" in external_creds:
            # 个人中心编辑 USP 账户时，未提供的字段需保留旧值，避免只改用户名却把已存储的密码哈希清空
            existing_ec = user.get('external_credentials', {}) or {}
            existing_usp = existing_ec.get('usp', {}) or {}
            if not external_creds["usp"].get("username"):
                external_creds["usp"]["username"] = existing_usp.get("username")
            new_password = external_creds["usp"].get("password")
            if new_password:  # 提供新明文密码 → 哈希后存储
                external_creds["usp"]["password"] = get_password_hash(new_password)
            elif "password" in existing_usp:  # 未提供新密码 → 保留旧密码哈希
                external_creds["usp"]["password"] = existing_usp["password"]
        update_data["external_credentials"] = external_creds
    if user_data.avatar_resource_id is not None:
        update_data["avatar_resource_id"] = user_data.avatar_resource_id
    if user_data.company_id is not None:
        update_data["company_id"] = user_data.company_id or None
    if user_data.department_id is not None:
        update_data["department_id"] = user_data.department_id or None
    if user_data.responsibility_modules is not None:
        update_data["responsibility_modules"] = user_data.responsibility_modules
    if user_data.job_level is not None:
        update_data["job_level"] = user_data.job_level
    if user_data.duty_text is not None:
        update_data["duty_text"] = user_data.duty_text
    if user_data.supervisor_id is not None:
        update_data["supervisor_id"] = user_data.supervisor_id or None

    success = db_manager.update_user(user['id'], **update_data)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="更新用户失败"
        )

    updated_user = db_manager.get_user(username)
    return User(
        id=updated_user['id'],
        username=updated_user['username'],
        permissions=updated_user['permissions'],
        roles=user.get('roles', {}),
        name=updated_user.get('name'),
        status=updated_user.get('status', 'inactive'),
        external_credentials=_mask_usp_password(updated_user.get('external_credentials', {})),
        avatar_resource_id=updated_user.get('avatar_resource_id'),
        company_id=updated_user.get('company_id'),
        department_id=updated_user.get('department_id'),
        company=updated_user.get('company'),
        department=updated_user.get('department'),
        responsibility_modules=updated_user.get('responsibility_modules', {}),
        job_level=updated_user.get('job_level', 1),
        duty_text=updated_user.get('duty_text'),
        supervisor_id=updated_user.get('supervisor_id'),
    )

@router.delete("/{username}", response_model=SuccessResponse)
async def delete_user(
    username: str,
    current_user: Dict[str, Any] = require_permission("backend:user:base:delete")
):
    user = db_manager.get_user(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )
    
    if user['username'] == current_user['username']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能删除自己的账号"
        )
    
    success = db_manager.delete_user(user['id'])
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="删除用户失败"
        )
    
    return SuccessResponse(message=f"用户 {username} 已删除")

@router.post("/{username}/roles", response_model=SuccessResponse, summary="为用户批量分配角色")
async def assign_role(
    username: str,
    role_data: RoleAssignment,
    current_user: Dict[str, Any] = require_permission("backend:user:role_project:write")
):
    user = db_manager.get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    if not db_manager.get_project(role_data.project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    
    if not role_data.role_ids:
        raise HTTPException(status_code=400, detail="角色ID列表不能为空")
    
    try:
        assigned_count = 0
        for role_id in role_data.role_ids:
            role = db_manager.get_role(role_id)
            if not role:
                raise HTTPException(status_code=404, detail=f"角色 {role_id} 不存在")
            
            user_project_role_id = f"upr_{user['id']}_{role_data.project_id}_{role_id}"
            
            success = db_manager.add_user_project_role(
                user_project_role_id, 
                user['id'], 
                role_data.project_id, 
                role_id,
                report_to_id=role_data.report_to_id
            )
            if success:
                assigned_count += 1
        
        return SuccessResponse(message=f"成功分配 {assigned_count} 个角色")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"角色分配失败: {str(e)}")

@router.post("/{username}/roles/remove", response_model=SuccessResponse, summary="批量删除用户在特定项目中的角色")
async def remove_user_roles_batch(
    username: str,
    role_data: RoleBatchRemoval,
    current_user: Dict[str, Any] = require_permission("backend:user:role_project:write")
):
    user = db_manager.get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    if not role_data.role_ids:
        raise HTTPException(status_code=400, detail="角色ID列表不能为空")
    
    try:
        removed_count = 0
        for role_id in role_data.role_ids:
            success = db_manager.remove_user_project_role(
                user['id'], 
                role_data.project_id, 
                role_id
            )
            if success:
                removed_count += 1
        
        return SuccessResponse(message=f"成功移除用户在项目中的{removed_count}个角色")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量移除角色失败: {str(e)}")

@router.get("/{username}/reporters", response_model=List[Dict[str, Any]], summary="获取用户在项目中的所有层级汇报人")
async def get_user_reporters(
    username: str,
    project_id: str = Query(..., description="项目ID"),
    current_user: Dict[str, Any] = require_permission("backend:user:base:read")
):
    user = db_manager.get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    
    if not db_manager.get_project(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    
    reporters = db_manager.get_all_reporters(username, project_id)
    
    return reporters

@router.post("/{username}/uspinfo", response_model=User)
async def update_user_uspinfo(
    username: str,
    user_data: UserUpdate,
    current_user: Dict[str, Any] = Depends(get_current_active_user_from_token)
):
    if current_user['username'] != username and "admin" not in current_user.get('permissions', []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="没有权限更新该用户信息"
        )

    user = db_manager.get_user(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )
    
    update_data = {}
    usp_password = None
    if user_data.name is not None:
        update_data["name"] = user_data.name
        usp_name = chinese_to_pinyin(user_data.name)
        
        db = db_manager.get_db()
        try:
            unique_usp_name = check_and_generate_unique_usp_name(db, usp_name, exclude_user_id=user['id'])
            
            usp_password = generate_password(unique_usp_name)
            usp_password_hash = get_password_hash(usp_password)
            data = {"usp":{
            "username": unique_usp_name,
            "password": usp_password_hash
            }}
            update_data["external_credentials"] = data
        finally:
            db.close()
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="姓名不能为空"
        )
    
    success = db_manager.update_user(user['id'], **update_data)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="更新用户失败"
        )
    
    updated_user = db_manager.get_user(username)
    external_credentials = updated_user.get('external_credentials', {})
    if 'usp' in external_credentials and usp_password:
        external_credentials['usp']['password'] = usp_password
    return User(
        id=updated_user['id'],
        username=updated_user['username'],
        permissions=updated_user['permissions'],
        roles=user.get('roles', {}),
        name=updated_user.get('name'),
        status=updated_user.get('status', 'inactive'),
        external_credentials=external_credentials,
        avatar_resource_id=updated_user.get('avatar_resource_id')
    )

@router.post("/project/assign-roles", response_model=SuccessResponse, summary="为项目批量分配用户角色和汇报人")
async def batch_assign_project_roles(
    role_data: ProjectUserRoleAssignment,
    current_user: Dict[str, Any] = require_permission("backend:user:role_project:write")
):
    project_id = role_data.project_id.strip() if role_data.project_id else None
    
    if project_id and not db_manager.get_project(project_id):
        raise HTTPException(status_code=404, detail="项目不存在")
    
    if not role_data.organization_ids:
        raise HTTPException(status_code=400, detail="用户列表不能为空")
    
    try:
        roles_data = []
        for item in role_data.organization_ids:
            if "user_name" not in item or "role_id" not in item:
                raise HTTPException(status_code=400, detail="用户列表项必须包含 user_name 和 role_id")
            
            user = db_manager.get_user(item["user_name"])
            if not user:
                raise HTTPException(status_code=404, detail=f"用户 {item['user_name']} 不存在")
            
            role = db_manager.get_role(item["role_id"])
            if not role:
                raise HTTPException(status_code=404, detail=f"角色 {item['role_id']} 不存在")
            
            user_project_role_id = f"upr_{user['id']}_{project_id or 'global'}_{item['role_id']}"

            # report_to_id 前端传的是 username，需解析为 user_id 以满足外键约束 ForeignKey('users.id')
            report_to_username = item.get("report_to_id")
            report_to_id = None
            if report_to_username:
                superior = db_manager.get_user(report_to_username)
                if not superior:
                    raise HTTPException(status_code=404, detail=f"上级用户 {report_to_username} 不存在")
                report_to_id = superior['id']

            roles_data.append({
                "id": user_project_role_id,
                "user_id": user['id'],
                "project_id": project_id,
                "role_id": item["role_id"],
                "report_to_id": report_to_id,
            })
        
        assigned_count = db_manager.batch_add_user_project_roles(roles_data)

        scope = f"项目 {project_id}" if project_id else "全局"
        return SuccessResponse(message=f"成功为{scope}分配 {assigned_count} 个用户角色")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"角色分配失败: {str(e)}")

@router.delete("/project/role", response_model=SuccessResponse, summary="移除用户在项目中的角色绑定")
async def remove_user_project_role(
    user_id: str = Query(..., description="用户ID"),
    project_id: str = Query(..., description="项目ID（全局角色传 global）"),
    role_id: str = Query(..., description="角色ID"),
    current_user: Dict[str, Any] = require_permission("backend:user:role_project:write")
):
    # 'global' 是列表接口的占位值，底层存储用 None
    actual_project_id = None if project_id == 'global' else project_id
    if not db_manager.remove_user_project_role(user_id, actual_project_id, role_id):
        raise HTTPException(status_code=500, detail="移除用户项目角色失败")
    return SuccessResponse(message="已移除用户项目角色")

@router.post("/migrate-user", response_model=SuccessResponse, summary="迁移用户数据并删除源用户")
async def migrate_user(
    payload: Dict[str, Any] = Body(...),
    current_user: Dict[str, Any] = require_permission("backend:user:base:write")
):
    """迁移用户数据：
    入参 source_user_id（A用户）和 target_user_id（B用户）。
    1. 将assigned_to为A用户username的task改为B用户的username
    2. 将A用户的 department / responsibility_modules / job_level / duty_text 字段拷贝给B用户
    3. 删除A用户
    """
    source_user_id = payload.get("source_user_id")
    target_user_id = payload.get("target_user_id")

    if not source_user_id or not target_user_id:
        raise HTTPException(status_code=400, detail="source_user_id 和 target_user_id 不能为空")
    if source_user_id == target_user_id:
        raise HTTPException(status_code=400, detail="源用户和目标用户不能相同")

    db = db_manager.get_db()
    try:
        # 1. 查询A用户和B用户
        user_a = db.query(UserDB).filter(UserDB.id == source_user_id).first()
        user_b = db.query(UserDB).filter(UserDB.id == target_user_id).first()

        if not user_a:
            raise HTTPException(status_code=404, detail=f"源用户不存在: {source_user_id}")
        if not user_b:
            raise HTTPException(status_code=404, detail=f"目标用户不存在: {target_user_id}")

        # 2. 查询assigned_to为A用户username的tasks并迁移
        tasks_to_migrate = db.query(Task).filter(
            Task.assigned_to == user_a.username
        ).all()

        migrated_count = 0
        if tasks_to_migrate:
            for task in tasks_to_migrate:
                task.assigned_to = user_b.username
            migrated_count = len(tasks_to_migrate)

        # 3. 将A用户的字段拷贝给B用户
        fields_copied = {}
        if user_a.department:
            user_b.department = user_a.department
            fields_copied["department"] = True
        if user_a.responsibility_modules:
            user_b.responsibility_modules = user_a.responsibility_modules
            fields_copied["responsibility_modules"] = True
        if user_a.job_level:
            user_b.job_level = user_a.job_level
            fields_copied["job_level"] = True
        if user_a.duty_text:
            user_b.duty_text = user_a.duty_text
            fields_copied["duty_text"] = True

        # 4. 迁移A用户的项目角色关联：
        #    a) user_id 为 A 的行（A 在各项目中的角色）→ 迁移到 B
        #       使用 INSERT ... ON DUPLICATE KEY UPDATE 处理 (user_id, project_id, role_id) 唯一约束冲突：
        #       若 B 已有相同 (project_id, role_id) 的角色行，则合并（用 A 的 report_to_id 补充），否则插入新行。
        #       注意：需在 DB 层存在 (user_id, project_id, role_id) 唯一索引，ON DUPLICATE KEY 才会触发合并。
        #    b) report_to_id 为 A 的行（原本向 A 汇报的人）→ 更新为 B 的 id
        a_role_rows = db.execute(
            user_project_roles.select().where(
                user_project_roles.c.user_id == source_user_id
            )
        ).fetchall()

        role_rows_migrated = 0   # 新插入到 B 的行数
        role_rows_merged = 0     # 合并到 B 已有行的数量
        for row in a_role_rows:
            new_id = str(uuid.uuid4())
            result = db.execute(
                text("""
                    INSERT INTO user_project_roles (id, user_id, project_id, role_id, report_to_id)
                    VALUES (:id, :user_id, :project_id, :role_id, :report_to_id)
                    ON DUPLICATE KEY UPDATE
                        report_to_id = COALESCE(VALUES(report_to_id), report_to_id)
                """),
                {
                    "id": new_id,
                    "user_id": target_user_id,
                    "project_id": row.project_id,
                    "role_id": row.role_id,
                    "report_to_id": row.report_to_id,
                }
            )
            # MySQL ON DUPLICATE KEY UPDATE: rowcount=1 → 新插入, 2 → 更新已有行, 0 → 无变化
            if result.rowcount == 1:
                role_rows_migrated += 1
            else:
                role_rows_merged += 1

        # 删除 A 的原始角色行（已迁移到 B 或合并到 B 已有行）
        db.execute(
            user_project_roles.delete().where(
                user_project_roles.c.user_id == source_user_id
            )
        )

        report_to_result = db.execute(
            user_project_roles.update().where(
                user_project_roles.c.report_to_id == source_user_id
            ).values(report_to_id=target_user_id)
        )
        report_to_rows_migrated = report_to_result.rowcount

        # 5. 删除A用户
        db.delete(user_a)
        db.commit()

        return SuccessResponse(
            message=f"成功迁移用户 {user_a.username} → {user_b.username}，"
                    f"迁移任务 {migrated_count} 个，拷贝字段 {len(fields_copied)} 项，"
                    f"迁移项目角色 {role_rows_migrated} 项、合并 {role_rows_merged} 项、"
                    f"汇报关系 {report_to_rows_migrated} 项，已删除源用户"
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"迁移用户失败:{traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"迁移用户失败: {str(e)}"
        )
    finally:
        db.close()