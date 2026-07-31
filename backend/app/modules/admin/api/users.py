from fastapi import APIRouter, Depends, HTTPException, Request, status, Query, Body
from typing import List, Dict, Any
import uuid
import traceback
from sqlalchemy import text

from typing import Dict, Any
from app.core.database import db_manager, get_user_with_roles, UserDB
from app.core.security import get_password_hash
from app.modules.admin.schemas.user import User, UserCreate, UserUpdate, UserDetail
from app.modules.admin.schemas.role import RoleBatchRemoval, RoleAssignment
from app.modules.admin.schemas.project import ProjectUserRoleAssignment
from app.modules.admin.schemas.response import SuccessResponse
from app.modules.admin.api.auth import get_current_active_user_from_token, require_permission
from app.services.hmac_utils import generate_password, chinese_to_pinyin
from app.models.task import Task

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
        department=user_data.department,
        responsibility_modules=user_data.responsibility_modules,
        job_level=user_data.job_level,
        duty_text=user_data.duty_text,
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
        external_credentials=created_user.get('external_credentials', {}),
        avatar_resource_id=created_user.get('avatar_resource_id')
    )

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
        external_credentials=user.get('external_credentials', {}),
        avatar_resource_id=user.get('avatar_resource_id')
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
        if "usp" in external_creds and "password" in external_creds["usp"]:
            usp_password = external_creds["usp"]["password"]
            external_creds["usp"]["password"] = get_password_hash(usp_password)
        update_data["external_credentials"] = external_creds
    if user_data.avatar_resource_id is not None:
        update_data["avatar_resource_id"] = user_data.avatar_resource_id
    if user_data.department is not None:
        update_data["department"] = user_data.department
    if user_data.responsibility_modules is not None:
        update_data["responsibility_modules"] = user_data.responsibility_modules
    if user_data.job_level is not None:
        update_data["job_level"] = user_data.job_level
    if user_data.duty_text is not None:
        update_data["duty_text"] = user_data.duty_text

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
        external_credentials=updated_user.get('external_credentials', {}),
        avatar_resource_id=updated_user.get('avatar_resource_id')
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
            
            roles_data.append({
                "id": user_project_role_id,
                "user_id": user['id'],
                "project_id": project_id,
                "role_id": item["role_id"],
                "report_to_id": item.get("report_to_id")
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

        # 4. 删除A用户
        db.delete(user_a)
        db.commit()

        return SuccessResponse(
            message=f"成功迁移用户 {user_a.username} → {user_b.username}，"
                    f"迁移任务 {migrated_count} 个，拷贝字段 {len(fields_copied)} 项，已删除源用户"
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