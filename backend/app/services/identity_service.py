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
        user_id: str,
        username: str,
        hashed_password: str,
        permissions: List[str],
        name: Optional[str] = None,
        status: str = "inactive",
        external_credentials: Optional[Dict[str, Dict[str, str]]] = None
    ) -> bool:
        db = IdentityService._get_db()
        try:
            existing_user = db.query(UserDB).filter(UserDB.username == username).first()
            if existing_user:
                return False
            
            external_credentials_json = json.dumps(external_credentials) if external_credentials else None
            
            db_user = UserDB(
                id=user_id,
                username=username,
                password_hash=hashed_password,
                name=name,
                status=status,
                external_credentials=external_credentials_json
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
                external_credentials = {}
                if hasattr(db_user, 'external_credentials') and db_user.external_credentials:
                    try:
                        external_credentials = json.loads(db_user.external_credentials)
                    except:
                        external_credentials = {}
                
                return {
                    'id': db_user.id,
                    'username': db_user.username,
                    'password_hash': db_user.password_hash,
                    'name': getattr(db_user, 'name', None),
                    'status': getattr(db_user, 'status', 'inactive'),
                    'external_credentials': external_credentials,
                    'permissions': ["admin"] if db_user.username == 'admin' else ["user"]
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
                external_credentials = {}
                if hasattr(db_user, 'external_credentials') and db_user.external_credentials:
                    try:
                        external_credentials = json.loads(db_user.external_credentials)
                    except:
                        external_credentials = {}
                
                return {
                    'id': db_user.id,
                    'username': db_user.username,
                    'password_hash': db_user.password_hash,
                    'name': getattr(db_user, 'name', None),
                    'status': getattr(db_user, 'status', 'inactive'),
                    'external_credentials': external_credentials,
                    'permissions': ["admin"] if db_user.username == 'admin' else ["user"]
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
    def add_role(role_id: str, role_name: str) -> bool:
        db = IdentityService._get_db()
        try:
            existing_role = db.query(Role).filter(
                (Role.id == role_id) | (Role.name == role_name)
            ).first()
            if existing_role:
                return False
            
            role = Role(id=role_id, name=role_name)
            db.add(role)
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
            if role:
                return {'id': role.id, 'name': role.name}
            return None
        finally:
            db.close()

    @staticmethod
    def get_all_roles() -> List[Dict[str, str]]:
        db = IdentityService._get_db()
        try:
            roles = db.query(Role).all()
            return [{'id': role.id, 'name': role.name} for role in roles]
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
            projects = db.query(Project).all()
            return [{'id': project.id, 'code': project.code, 'name': project.name} for project in projects]
        finally:
            db.close()

    @staticmethod
    def get_project(project_id: str) -> Optional[Dict[str, str]]:
        db = IdentityService._get_db()
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                return {'id': project.id, 'code': project.code, 'name': project.name}
            return None
        finally:
            db.close()

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
    def add_user_project_role(
        user_project_role_id: str,
        user_id: str,
        project_id: Optional[str],
        role_id: str,
        report_to_id: Optional[str] = None
    ) -> bool:
        db = IdentityService._get_db()
        try:
            if project_id:
                existing_upr = db.execute(user_project_roles.select().where(
                    (user_project_roles.c.user_id == user_id) &
                    (user_project_roles.c.project_id == project_id) &
                    (user_project_roles.c.role_id == role_id)
                )).first()
            else:
                existing_upr = db.execute(user_project_roles.select().where(
                    (user_project_roles.c.user_id == user_id) &
                    (user_project_roles.c.project_id.is_(None)) &
                    (user_project_roles.c.role_id == role_id)
                )).first()
            
            if existing_upr:
                return False
            
            db.execute(user_project_roles.insert().values(
                id=user_project_role_id,
                user_id=user_id,
                project_id=project_id,
                role_id=role_id,
                report_to_id=report_to_id
            ))
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            print(f"添加用户-项目-角色关联失败: {str(e)}")
            return False
        finally:
            db.close()

    @staticmethod
    def batch_add_user_project_roles(roles_data: List[Dict[str, str]]) -> int:
        db = IdentityService._get_db()
        try:
            insert_data = []
            
            for data in roles_data:
                existing_upr = db.execute(user_project_roles.select().where(
                    (user_project_roles.c.user_id == data['user_id']) &
                    (user_project_roles.c.project_id == data['project_id']) &
                    (user_project_roles.c.role_id == data['role_id'])
                )).first()
                
                if not existing_upr:
                    insert_data.append(data)
            
            if insert_data:
                db.execute(user_project_roles.insert(), insert_data)
                db.commit()
                return len(insert_data)
            else:
                return 0
        except Exception as e:
            db.rollback()
            print(f"批量添加用户-项目-角色关联失败: {str(e)}")
            return 0
        finally:
            db.close()

    @staticmethod
    def remove_user_project_role(user_id: str, project_id: str, role_id: str) -> bool:
        db = IdentityService._get_db()
        try:
            result = db.execute(user_project_roles.delete().where(
                (user_project_roles.c.user_id == user_id) &
                (user_project_roles.c.project_id == project_id) &
                (user_project_roles.c.role_id == role_id)
            ))
            db.commit()
            return result.rowcount > 0
        except Exception as e:
            db.rollback()
            print(f"移除用户-项目-角色关联失败: {str(e)}")
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
                'action': permission.action
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
                if key in ['name', 'description', 'resource_type', 'action'] and value is not None:
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