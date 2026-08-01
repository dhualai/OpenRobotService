from typing import Dict, List, Optional, Any
import json
import uuid
from sqlalchemy.orm import Session
from sqlalchemy import select, text
from app.core.db import SessionLocal
from app.models import UserDB, Role, Permission, Project, role_permissions, user_project_roles
from app.core.security import get_password_hash, verify_password


class IdentityService:
    @staticmethod
    def _get_db() -> Session:
        db = SessionLocal()
        try:
            return db
        except:
            db.close()
            raise

    @staticmethod
    def add_user(
        user_id: str, username: str, hashed_password: str, permissions: List[str],
        name: Optional[str] = None, status: str = "inactive",
        external_credentials: Optional[Dict[str, Dict[str, str]]] = None,
        department: Optional[str] = None, responsibility_modules: Optional[Dict[str, List[str]]] = None,
        job_level: Optional[int] = 1, duty_text: Optional[str] = None,
    ) -> bool:
        db = IdentityService._get_db()
        try:
            existing_user = db.query(UserDB).filter(UserDB.username == username).first()
            if existing_user:
                return False
            external_credentials_json = json.dumps(external_credentials) if external_credentials else None
            db_user = UserDB(
                id=user_id, username=username, password_hash=hashed_password,
                name=name, status=status, external_credentials=external_credentials_json,
                department=department, responsibility_modules=responsibility_modules or {},
                job_level=job_level if job_level is not None else 1, duty_text=duty_text,
            )
            db.add(db_user)
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"添加用户失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def get_user(username: str) -> Optional[Dict[str, Any]]:
        db = IdentityService._get_db()
        try:
            db_user = db.query(UserDB).filter(UserDB.username == username).first()
            if db_user:
                ec = {}
                if hasattr(db_user, 'external_credentials') and db_user.external_credentials:
                    try: ec = json.loads(db_user.external_credentials)
                    except: ec = {}
                return {
                    'id': db_user.id, 'username': db_user.username,
                    'password_hash': db_user.password_hash,
                    'name': getattr(db_user, 'name', None),
                    'status': getattr(db_user, 'status', 'inactive'),
                    'external_credentials': ec,
                    'avatar_resource_id': getattr(db_user, 'avatar_resource_id', None),
                    'permissions': ["admin"] if db_user.username == 'admin' else ["user"],
                    'department': getattr(db_user, 'department', None),
                    'responsibility_modules': getattr(db_user, 'responsibility_modules', None) or {},
                    'job_level': getattr(db_user, 'job_level', 1),
                    'duty_text': getattr(db_user, 'duty_text', None),
                }
            return None
        finally:
            db.close()

    @staticmethod
    def get_user_by_id(user_id: str) -> Optional[Dict[str, Any]]:
        db = IdentityService._get_db()
        try:
            db_user = db.query(UserDB).filter(UserDB.id == user_id).first()
            if db_user:
                ec = {}
                if hasattr(db_user, 'external_credentials') and db_user.external_credentials:
                    try: ec = json.loads(db_user.external_credentials)
                    except: ec = {}
                return {
                    'id': db_user.id, 'username': db_user.username,
                    'password_hash': db_user.password_hash,
                    'name': getattr(db_user, 'name', None),
                    'status': getattr(db_user, 'status', 'inactive'),
                    'external_credentials': ec,
                    'avatar_resource_id': getattr(db_user, 'avatar_resource_id', None),
                    'permissions': ["admin"] if db_user.username == 'admin' else ["user"],
                    'department': getattr(db_user, 'department', None),
                    'responsibility_modules': getattr(db_user, 'responsibility_modules', None) or {},
                    'job_level': getattr(db_user, 'job_level', 1),
                    'duty_text': getattr(db_user, 'duty_text', None),
                }
            return None
        finally:
            db.close()

    @staticmethod
    def update_user(user_id: str, **kwargs) -> bool:
        db = IdentityService._get_db()
        try:
            db_user = db.query(UserDB).filter(UserDB.id == user_id).first()
            if not db_user:
                return False
            for key, value in kwargs.items():
                if key == 'external_credentials' and value is not None:
                    setattr(db_user, key, json.dumps(value))
                elif hasattr(db_user, key):
                    setattr(db_user, key, value)
            db.commit()
            from app.services.user_service import UserService
            UserService.invalidate_cache()
            return True
        except Exception as e:
            db.rollback()
            print(f"更新用户失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def delete_user(user_id: str) -> bool:
        db = IdentityService._get_db()
        try:
            db_user = db.query(UserDB).filter(UserDB.id == user_id).first()
            if db_user:
                db.delete(db_user)
                db.commit()
                return True
            return False
        except Exception as e:
            db.rollback()
            print(f"删除用户失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def add_role(role_id: str, role_name: str, role_type: str = 'project') -> bool:
        db = IdentityService._get_db()
        try:
            if db.query(Role).filter((Role.id == role_id) | (Role.name == role_name)).first():
                return False
            db.add(Role(id=role_id, name=role_name, role_type=role_type))
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"添加角色失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def get_role(role_id: str) -> Optional[Dict[str, str]]:
        db = IdentityService._get_db()
        try:
            role = db.query(Role).filter(Role.id == role_id).first()
            return {'id': role.id, 'name': role.name, 'role_type': role.role_type} if role else None
        finally:
            db.close()

    @staticmethod
    def get_all_roles() -> List[Dict[str, str]]:
        db = IdentityService._get_db()
        try:
            return [{'id': r.id, 'name': r.name, 'role_type': r.role_type} for r in db.query(Role).all()]
        finally:
            db.close()

    @staticmethod
    def update_role(role_id: str, name: Optional[str] = None, role_type: Optional[str] = None) -> bool:
        db = IdentityService._get_db()
        try:
            role = db.query(Role).filter(Role.id == role_id).first()
            if not role:
                return False
            if name is not None:
                role.name = name
            if role_type is not None:
                role.role_type = role_type
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"更新角色失败: {str(e)}")
            return False
        finally:
            db.close()

    # 按角色名称关键词自动判断类型：系统角色（跨项目/平台级）优先匹配，其次项目角色；
    # 未命中任何关键词的角色保持原有分类不变，避免误判。
    _SYSTEM_ROLE_KEYWORDS = [
        '管理员', '超级', '超管', 'admin', '运维', '系统', '平台', 'devops', '技术支持', '后台',
    ]
    _PROJECT_ROLE_KEYWORDS = [
        '项目经理', '项目负责人', '现场', '实施', '工程师', '客户', '售后', '组长', '专员', '项目', 'pm',
    ]

    @staticmethod
    def classify_role_type(name: str) -> Optional[str]:
        lowered = (name or '').lower()
        for kw in IdentityService._SYSTEM_ROLE_KEYWORDS:
            if kw.lower() in lowered:
                return 'system'
        for kw in IdentityService._PROJECT_ROLE_KEYWORDS:
            if kw.lower() in lowered:
                return 'project'
        return None

    @staticmethod
    def auto_classify_roles() -> List[Dict[str, str]]:
        """按名称关键词批量重新分类所有角色，返回发生变更的角色列表（含 old_type/new_type）。"""
        db = IdentityService._get_db()
        changed: List[Dict[str, str]] = []
        try:
            for role in db.query(Role).all():
                suggested = IdentityService.classify_role_type(role.name)
                if suggested and suggested != role.role_type:
                    changed.append({
                        'id': role.id, 'name': role.name,
                        'old_type': role.role_type, 'new_type': suggested,
                    })
                    role.role_type = suggested
            if changed:
                db.commit()
            return changed
        except Exception as e:
            db.rollback()
            print(f"自动分类角色失败: {str(e)}")
            return []
        finally:
            db.close()

    @staticmethod
    def delete_role(role_id: str) -> bool:
        db = IdentityService._get_db()
        try:
            role = db.query(Role).filter(Role.id == role_id).first()
            if not role:
                return False
            db.delete(role)
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"删除角色失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def get_all_projects() -> List[Dict[str, str]]:
        db = IdentityService._get_db()
        try:
            return [{'id': p.id, 'code': p.code, 'name': p.name} for p in db.query(Project).all()]
        finally:
            db.close()

    @staticmethod
    def get_project(project_id: str) -> Optional[Dict[str, Any]]:
        db = IdentityService._get_db()
        try:
            p = db.query(Project).filter(Project.id == project_id).first()
            return {'id': p.id, 'code': p.code, 'name': p.name} if p else None
        finally:
            db.close()

    @staticmethod
    def get_user_roles_all_projects(user_id: str) -> Dict[str, List[str]]:
        db = IdentityService._get_db()
        try:
            roles = db.execute(user_project_roles.select().where(
                user_project_roles.c.user_id == user_id)).fetchall()
            rbp = {}
            for r in roles:
                pid = r.project_id or 'global'
                rbp.setdefault(pid, []).append(r.role_id)
            return rbp
        finally:
            db.close()

    @staticmethod
    def get_all_users_roles_all_projects(user_ids: List[str]) -> Dict[str, Dict[str, List[str]]]:
        db = IdentityService._get_db()
        try:
            roles = db.execute(user_project_roles.select().where(
                user_project_roles.c.user_id.in_(user_ids))).fetchall()
            ar = {}
            for r in roles:
                ar.setdefault(r.user_id, {}).setdefault(r.project_id or 'global', []).append(r.role_id)
            for uid in user_ids:
                ar.setdefault(uid, {})
            return ar
        finally:
            db.close()

    @staticmethod
    def add_user_project_role(upid, uid, pid, rid, report_to_id=None) -> bool:
        db = IdentityService._get_db()
        try:
            existing = db.execute(user_project_roles.select().where(
                (user_project_roles.c.id == upid))).first()
            if existing:
                return False
            vals = {"id": upid, "user_id": uid, "project_id": pid, "role_id": rid}
            if report_to_id:
                vals["report_to_id"] = report_to_id
            db.execute(user_project_roles.insert().values(**vals))
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            return False
        finally:
            db.close()

    @staticmethod
    def batch_add_user_project_roles(roles_data: List[dict]) -> int:
        db = IdentityService._get_db()
        try:
            count = 0
            for rd in roles_data:
                existing = db.execute(user_project_roles.select().where(
                    user_project_roles.c.id == rd["id"])).first()
                if existing:
                    continue
                db.execute(user_project_roles.insert().values(**rd))
                count += 1
            db.commit()
            return count
        except Exception as e:
            db.rollback()
            return 0
        finally:
            db.close()

    @staticmethod
    def remove_user_project_role(user_id, project_id, role_id) -> bool:
        db = IdentityService._get_db()
        try:
            db.execute(user_project_roles.delete().where(
                (user_project_roles.c.user_id == user_id) &
                (user_project_roles.c.project_id == project_id) &
                (user_project_roles.c.role_id == role_id)))
            db.commit()
            return True
        except:
            db.rollback()
            return False
        finally:
            db.close()

    @staticmethod
    def get_all_reporters(username: str, project_id: str) -> List[Dict[str, Any]]:
        from app.services.permission_service import permission_service
        return permission_service.get_all_reporters(username, project_id)

    @staticmethod
    def add_project(project_id: str, project_code: str, project_name: str) -> bool:
        db = IdentityService._get_db()
        try:
            existing_project = db.query(Project).filter(
                (Project.id == project_id) | (Project.code == project_code) | (Project.name == project_name)
            ).first()
            if existing_project:
                return False

            project = Project(id=project_id, code=project_code, name=project_name)
            db.add(project)
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"添加项目失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def update_project(project_id: str, project_name: str) -> bool:
        db = IdentityService._get_db()
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                return False

            existing_project = db.query(Project).filter(
                Project.name == project_name, Project.id != project_id
            ).first()
            if existing_project:
                return False

            project.name = project_name
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"更新项目失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def delete_project(project_id: str) -> bool:
        db = IdentityService._get_db()
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if not project:
                return False

            db.execute(user_project_roles.delete().where(
                user_project_roles.c.project_id == project_id
            ))

            db.delete(project)
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"删除项目失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def add_role_permission(role_permission_id: str, role_id: str, permission_id: str) -> bool:
        db = IdentityService._get_db()
        try:
            existing_perm = db.execute(role_permissions.select().where(
                (role_permissions.c.role_id == role_id) &
                (role_permissions.c.permission_id == permission_id)
            )).first()

            if existing_perm:
                return False

            db.execute(role_permissions.insert().values(
                id=role_permission_id,
                role_id=role_id,
                permission_id=permission_id
            ))
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"添加角色权限失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def remove_role_permission(role_id: str, permission_id: str) -> bool:
        db = IdentityService._get_db()
        try:
            result = db.execute(role_permissions.delete().where(
                (role_permissions.c.role_id == role_id) &
                (role_permissions.c.permission_id == permission_id)
            ))
            db.commit()
            return result.rowcount > 0
        except Exception as e:
            db.rollback()
            print(f"删除角色权限失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def get_all_permissions() -> List[Dict[str, Any]]:
        db = IdentityService._get_db()
        try:
            permissions = db.query(Permission).all()
            return [{
                'id': permission.id,
                'code': permission.code,
                'name': permission.name,
                'description': permission.description,
                'resource_type': permission.resource_type,
                'action': permission.action,
                'enabled': permission.enabled == "true"
            } for permission in permissions]
        finally:
            db.close()

    @staticmethod
    def add_permission(
        permission_id: str,
        code: str,
        name: str,
        resource_type: str,
        action: str,
        description: Optional[str] = None
    ) -> bool:
        db = IdentityService._get_db()
        try:
            existing_permission = db.query(Permission).filter(
                (Permission.id == permission_id) | (Permission.code == code)
            ).first()
            if existing_permission:
                return False

            permission = Permission(
                id=permission_id,
                code=code,
                name=name,
                resource_type=resource_type,
                action=action,
                description=description
            )
            db.add(permission)
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"添加权限失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def delete_permission(permission_id: str) -> bool:
        db = IdentityService._get_db()
        try:
            permission = db.query(Permission).filter(Permission.id == permission_id).first()
            if not permission:
                return False

            db.execute(role_permissions.delete().where(
                role_permissions.c.permission_id == permission_id
            ))

            db.delete(permission)
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"删除权限失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def update_permission(permission_id: str, **kwargs) -> bool:
        db = IdentityService._get_db()
        try:
            permission = db.query(Permission).filter(Permission.id == permission_id).first()
            if not permission:
                return False

            update_fields = {}
            for key, value in kwargs.items():
                if key in ['code', 'name', 'description', 'resource_type', 'action', 'enabled'] and value is not None:
                    if key == 'enabled':
                        update_fields[key] = "true" if value else "false"
                    else:
                        update_fields[key] = value

            if not update_fields:
                return True

            db.query(Permission).filter(Permission.id == permission_id).update(update_fields)
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"更新权限失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def get_project_members(project_id: str, include_usp: bool = False) -> List[Dict[str, Any]]:
        db = IdentityService._get_db()
        try:
            report_to_subquery = select(UserDB.username).where(
                UserDB.username == user_project_roles.c.report_to_id
            ).correlate(user_project_roles).scalar_subquery()

            if include_usp:
                stmt = select(
                    Role.name.label('role_name'),
                    UserDB.id.label('user_id'),
                    UserDB.username,
                    UserDB.name,
                    UserDB.external_credentials,
                    user_project_roles.c.project_id,
                    user_project_roles.c.role_id,
                    report_to_subquery.label('report_to_name')
                ).select_from(user_project_roles).join(
                    Role, user_project_roles.c.role_id == Role.id
                ).join(
                    UserDB, user_project_roles.c.user_id == UserDB.id
                ).where(user_project_roles.c.project_id == project_id)
            else:
                stmt = select(
                    Role.name.label('role_name'),
                    UserDB.id.label('user_id'),
                    UserDB.username,
                    UserDB.name,
                    user_project_roles.c.project_id,
                    user_project_roles.c.role_id,
                    report_to_subquery.label('report_to_name')
                ).select_from(user_project_roles).join(
                    Role, user_project_roles.c.role_id == Role.id
                ).join(
                    UserDB, user_project_roles.c.user_id == UserDB.id
                ).where(user_project_roles.c.project_id == project_id)

            try:
                results = db.execute(stmt).fetchall()
            except Exception as e:
                import traceback
                print(f"查询项目成员失败: {traceback.format_exc()}")
                return []

            members = []
            for result in results:
                member = {
                    'user_id': result.user_id,
                    'role_name': result.role_name,
                    'username': result.username,
                    'name': result.name,
                    'project_id': result.project_id,
                    'role_id': result.role_id,
                    'report_to_name': result.report_to_name,
                    'external_credentials': json.loads(result.external_credentials) if hasattr(result, 'external_credentials') and result.external_credentials else []
                }
                members.append(member)

            return members
        finally:
            db.close()

    @staticmethod
    def get_report_to_map(project_id: str) -> Dict[str, str]:
        """批量查询项目成员的 report_to_id 映射 {user_id: report_to_id}。

        get_project_members 的 report_to_name 子查询用 username 匹配 user_id 存在 bug，
        此方法直接取 user_project_roles.report_to_id 列，供路由层覆盖 report_to_name 字段。
        """
        db = IdentityService._get_db()
        try:
            stmt = select(
                user_project_roles.c.user_id,
                user_project_roles.c.report_to_id,
            ).where(
                user_project_roles.c.project_id == project_id,
                user_project_roles.c.report_to_id.isnot(None),
            )
            results = db.execute(stmt).fetchall()
            return {r.user_id: r.report_to_id for r in results}
        finally:
            db.close()

    @staticmethod
    def get_users_by_role(role_id: str) -> List[Dict[str, str]]:
        db = IdentityService._get_db()
        try:
            user_roles = db.execute(user_project_roles.select().where(
                user_project_roles.c.role_id == role_id
            )).fetchall()

            unique_user_ids = set(ur.user_id for ur in user_roles)

            users = []
            for user_id in unique_user_ids:
                user = IdentityService.get_user_by_id(user_id)
                if user:
                    users.append({
                        'id': user_id,
                        'username': user['username']
                    })

            return users
        finally:
            db.close()


identity_service = IdentityService()
