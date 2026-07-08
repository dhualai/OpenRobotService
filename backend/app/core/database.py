from typing import Dict, List, Optional, Any
import json
import traceback
from sqlalchemy import select, func    
from sqlalchemy import create_engine, Column, String, Text, ForeignKey, Table, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from .models import User
from .config import settings

Base = declarative_base()
DB_CONFIG = settings.DB_CONFIG
DATABASE_URL = f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

ASYNC_DATABASE_URL = DATABASE_URL.replace('mysql+pymysql', 'mysql+asyncmy')
async_engine = create_async_engine(
    ASYNC_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    echo=False
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False
)

role_permissions = Table(
    'role_permissions',
    Base.metadata,
    Column('id', String(64), primary_key=True),
    Column('role_id', String(64), ForeignKey('roles.id')),
    Column('permission_id', String(64), ForeignKey('permissions.id'))
)

user_project_roles = Table(
    'user_project_roles',
    Base.metadata,
    Column('id', String(64), primary_key=True),
    Column('user_id', String(64), ForeignKey('users.id')),
    Column('project_id', String(64), ForeignKey('projects.id')),
    Column('role_id', String(64), ForeignKey('roles.id')),
    Column('report_to_id', String(64), ForeignKey('users.id'), nullable=True)
)

class Role(Base):
    __tablename__ = "roles"
    
    id = Column(String(64), primary_key=True)
    name = Column(String(128), unique=True, nullable=False)

class Project(Base):
    __tablename__ = "projects"
    
    id = Column(String(64), primary_key=True)
    code = Column(String(64), unique=True, nullable=False)
    name = Column(String(128), unique=True, nullable=False)

class Permission(Base):
    __tablename__ = "permissions"
    
    id = Column(String(64), primary_key=True)
    code = Column(String(255), unique=True, nullable=False)
    name = Column(String(128), nullable=False)
    description = Column(Text, nullable=True)
    resource_type = Column(String(64), nullable=False)
    action = Column(String(64), nullable=False)

class UserDB(Base):
    __tablename__ = "users"
    
    id = Column(String(64), primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(128), nullable=True)
    status = Column(String(32), default="inactive", nullable=False)
    external_credentials = Column(Text, nullable=True)

class DatabaseManager:
    def __init__(self):
        Base.metadata.create_all(bind=engine)
    
    def get_db(self) -> Session:
        db = SessionLocal()
        try:
            return db
        except:
            db.close()
            raise
    
    def add_user(self, user_id: str, username: str, hashed_password: str, permissions: List[str], 
                 name: Optional[str] = None, status: str = "inactive", external_credentials: Optional[Dict[str, Dict[str, str]]] = None) -> bool:
        db = self.get_db()
        try:
            existing_user = db.query(UserDB).filter(UserDB.username == username).first()
            if existing_user:
                return False
            
            import json
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
    
    def get_user(self, username: str) -> Optional[User]:
        db = self.get_db()
        try:
            db_user = db.query(UserDB).filter(UserDB.username == username).first()
            if db_user:
                import json
                external_credentials = None
                if hasattr(db_user, 'external_credentials') and db_user.external_credentials:
                    try:
                        external_credentials = json.loads(db_user.external_credentials)
                    except:
                        external_credentials = {}
                
                from app.core.models import User
                user = User(
                    db_user.id,
                    db_user.username,
                    db_user.password_hash,
                    ["admin"] if db_user.username == 'admin' else ["user"],
                    name=getattr(db_user, 'name', None),
                    status=getattr(db_user, 'status', 'inactive'),
                    external_credentials=external_credentials
                )
                return user
            return None
        finally:
            db.close()
    
    def get_user_by_id(self, user_id: str) -> Optional[User]:
        db = self.get_db()
        try:
            db_user = db.query(UserDB).filter(UserDB.id == user_id).first()
            if db_user:
                import json
                external_credentials = None
                if hasattr(db_user, 'external_credentials') and db_user.external_credentials:
                    try:
                        external_credentials = json.loads(db_user.external_credentials)
                    except:
                        external_credentials = {}
                
                from app.core.models import User
                user = User(
                    db_user.id,
                    db_user.username,
                    db_user.password_hash,
                    ["admin"] if db_user.username == 'admin' else ["user"],
                    name=getattr(db_user, 'name', None),
                    status=getattr(db_user, 'status', 'inactive'),
                    external_credentials=external_credentials
                )
                return user
            return None
        finally:
            db.close()
    
    def update_user(self, user_id: str, **kwargs) -> bool:
        db = self.get_db()
        try:
            db_user = db.query(UserDB).filter(UserDB.id == user_id).first()
            if not db_user:
                return False
            
            for key, value in kwargs.items():
                if key == 'external_credentials' and value is not None:
                    import json
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
    
    def delete_user(self, user_id: str) -> bool:
        db = self.get_db()
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
    
    def add_role(self, role_id: str, role_name: str) -> bool:
        db = self.get_db()
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
    
    def get_role(self, role_id: str) -> Optional[Dict[str, str]]:
        db = self.get_db()
        try:
            role = db.query(Role).filter(Role.id == role_id).first()
            if role:
                return {'id': role.id, 'name': role.name}
            return None
        finally:
            db.close()
    
    def get_all_roles(self) -> List[Dict[str, str]]:
        db = self.get_db()
        try:
            roles = db.query(Role).all()
            return [{'id': role.id, 'name': role.name} for role in roles]
        finally:
            db.close()
    
    def get_all_projects(self) -> List[Dict[str, str]]:
        db = self.get_db()
        try:
            projects = db.query(Project).all()
            return [{'id': project.id, 'code': project.code, 'name': project.name} for project in projects]
        finally:
            db.close()
    
    def add_project(self, project_id: str, project_code: str, project_name: str) -> bool:
        db = self.get_db()
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
    
    def get_project(self, project_id: str) -> Optional[Dict[str, str]]:
        db = self.get_db()
        try:
            project = db.query(Project).filter(Project.id == project_id).first()
            if project:
                return {'id': project.id, 'code': project.code, 'name': project.name}
            return None
        finally:
            db.close()
    
    def update_project(self, project_id: str, project_name: str) -> bool:
        db = self.get_db()
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
    
    def delete_project(self, project_id: str) -> bool:
        db = self.get_db()
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
    
    def add_role_permission(self, role_permission_id: str, role_id: str, 
                           permission_id: str) -> bool:
        db = self.get_db()
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
    
    def remove_role_permission(self, role_id: str, permission_id: str) -> bool:
        db = self.get_db()
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
    
    def get_role_permissions(self, role_id: str) -> List[Dict[str, Any]]:
        db = self.get_db()
        try:
            permissions = db.execute(role_permissions.select().where(
                role_permissions.c.role_id == role_id
            )).fetchall()
            
            return [{
                'id': perm.id,
                'role_id': perm.role_id,
                'permission_id': perm.permission_id
            } for perm in permissions]
        finally:
            db.close()
    
    def check_role_permission(self, role_id: str, permission_id: str) -> bool:
        db = self.get_db()
        try:
            result = db.execute(role_permissions.select().where(
                (role_permissions.c.role_id == role_id) &
                (role_permissions.c.permission_id == permission_id)
            )).first()
            return result is not None
        except Exception:
            return False
        finally:
            db.close()
    
    def add_user_project_role(self, user_project_role_id: str, user_id: str, 
                           project_id: str, role_id: str, report_to_id: Optional[str] = None) -> bool:
        db = self.get_db()
        try:
            existing_upr = db.execute(user_project_roles.select().where(
                (user_project_roles.c.user_id == user_id) &
                (user_project_roles.c.project_id == project_id) &
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
    
    def batch_add_user_project_roles(self, roles_data: List[Dict[str, str]]) -> int:
        db = self.get_db()
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
    
    def remove_user_project_role(self, user_id: str, project_id: str, role_id: str) -> bool:
        db = self.get_db()
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
    
    def get_user_roles_by_project(self, user_id: str, project_id: str) -> List[str]:
        db = self.get_db()
        try:
            roles = db.execute(user_project_roles.select().where(
                (user_project_roles.c.user_id == user_id) &
                (user_project_roles.c.project_id == project_id)
            )).fetchall()
            
            return [role.role_id for role in roles]
        finally:
            db.close()
    
    def get_user_roles_all_projects(self, user_id: str) -> Dict[str, List[str]]:
        db = self.get_db()
        try:
            roles = db.execute(user_project_roles.select().where(
                user_project_roles.c.user_id == user_id
            )).fetchall()
            
            roles_by_project = {}
            for role in roles:
                if role.project_id not in roles_by_project:
                    roles_by_project[role.project_id] = []
                roles_by_project[role.project_id].append(role.role_id)
            
            return roles_by_project
        finally:
            db.close()
    
    def get_all_users_roles_all_projects(self, user_ids: List[str]) -> Dict[str, Dict[str, List[str]]]:
        db = self.get_db()
        try:
            roles = db.execute(user_project_roles.select().where(
                user_project_roles.c.user_id.in_(user_ids)
            )).fetchall()
            
            all_roles = {}
            for role in roles:
                if role.user_id not in all_roles:
                    all_roles[role.user_id] = {}
                if role.project_id not in all_roles[role.user_id]:
                    all_roles[role.user_id][role.project_id] = []
                all_roles[role.user_id][role.project_id].append(role.role_id)
            
            for user_id in user_ids:
                if user_id not in all_roles:
                    all_roles[user_id] = {}
            
            return all_roles
        finally:
            db.close()
    
    def get_user_report_to(self, user_id: str, project_id: str) -> Optional[str]:
        db = self.get_db()
        try:
            result = db.execute(user_project_roles.select().where(
                (user_project_roles.c.user_id == user_id) &
                (user_project_roles.c.project_id == project_id)
            )).first()
            
            if result and hasattr(result, 'report_to_id'):
                return result.report_to_id
            return None
        finally:
            db.close()
    
    def get_all_reporters(self, username: str, project_id: str) -> List[Dict[str, Any]]:
        db = self.get_db()
        try:
            user = db.query(UserDB).filter(UserDB.username == username).first()
            if not user:
                return []
            
            reporters = []
            current_user_id = user.id
            level = 1
            
            while current_user_id:
                result = db.execute(user_project_roles.select().where(
                    (user_project_roles.c.user_id == current_user_id) &
                    (user_project_roles.c.project_id == project_id)
                )).first()
                
                if not result or not getattr(result, 'report_to_id', None):
                    break
                
                report_to_user = db.query(UserDB).filter(UserDB.id == result.report_to_id).first()
                if report_to_user:
                    reporters.append({
                        'username': report_to_user.username,
                        'name': report_to_user.name,
                        'level': level
                    })
                    current_user_id = report_to_user.id
                    level += 1
                else:
                    break
            
            return reporters
        finally:
            db.close()
    
    def get_users_by_role(self, role_id: str) -> List[Dict[str, str]]:
        db = self.get_db()
        try:
            user_roles = db.execute(user_project_roles.select().where(
                user_project_roles.c.role_id == role_id
            )).fetchall()
            
            unique_user_ids = set(ur.user_id for ur in user_roles)
            
            users = []
            for user_id in unique_user_ids:
                user = self.get_user_by_id(user_id)
                if user:
                    users.append({
                        'id': user_id,
                        'username': user.username
                    })
            
            return users
        finally:
            db.close()
    
    def delete_role(self, role_id: str) -> bool:
        db = self.get_db()
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
    
    def get_permission(self, permission_id: str) -> Optional[Dict[str, Any]]:
        db = self.get_db()
        try:
            permission = db.query(Permission).filter(Permission.id == permission_id).first()
            if permission:
                return {
                    'id': permission.id,
                    'code': permission.code,
                    'name': permission.name,
                    'description': permission.description,
                    'resource_type': permission.resource_type,
                    'action': permission.action
                }
            return None
        finally:
            db.close()
    
    def get_all_permissions(self) -> List[Dict[str, Any]]:
        db = self.get_db()
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
    
    def get_user_permissions(self, user_id: str, project_id: str) -> List[Dict[str, Any]]:
        user_roles = self.get_user_roles_by_project(user_id, project_id)
        
        user_permissions = []
        for role_id in user_roles:
            role_permissions = self.get_role_permissions(role_id)
            for perm in role_permissions:
                permission_detail = self.get_permission(perm['permission_id'])
                if permission_detail:
                    user_permissions.append(permission_detail)
        
        return user_permissions
    
    def add_permission(self, permission_id: str, code: str, name: str, resource_type: str, action: str, 
                      description: Optional[str] = None) -> bool:
        db = self.get_db()
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
        
    def delete_permission(self, permission_id: str) -> bool:
        db = self.get_db()
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
            
    def update_permission(self, permission_id: str, **kwargs) -> bool:
        db = self.get_db()
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
    
    def get_project_members(self, project_id: str, include_usp: bool = False) -> List[Dict[str, Any]]:
        db = self.get_db()
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
                ).select_from(
                    user_project_roles
                ).join(
                    Role, user_project_roles.c.role_id == Role.id
                ).join(
                    UserDB, user_project_roles.c.user_id == UserDB.id
                ).where(
                    user_project_roles.c.project_id == project_id
                )
            else:
                stmt = select(
                    Role.name.label('role_name'),
                    UserDB.username,
                    UserDB.name,
                    user_project_roles.c.project_id,
                    user_project_roles.c.role_id,
                    report_to_subquery.label('report_to_name')
                ).select_from(
                    user_project_roles
                ).join(
                    Role, user_project_roles.c.role_id == Role.id
                ).join(
                    UserDB, user_project_roles.c.user_id == UserDB.id
                ).where(
                    user_project_roles.c.project_id == project_id
                )
            
            try:
                results = db.execute(stmt).fetchall()
            except Exception as e:
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

db_manager = DatabaseManager()

def init_users_db():
    from .security import get_password_hash
    admin_id = "user_admin"
    admin_username = "admin"
    admin_password = "123456"
    admin_permissions = ["admin", "permissions:*", "users:*"]
    
    if not db_manager.get_user(admin_username):
        db_manager.add_user(
            user_id=admin_id,
            username=admin_username,
            hashed_password=get_password_hash(admin_password),
            permissions=admin_permissions
        )

def get_projects_from_db() -> Dict[str, str]:
    projects = {}
    all_projects = db_manager.get_all_projects()
    for project in all_projects:
        projects[project['id']] = project['name']
    
    return projects

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def get_async_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def get_user_with_roles(username: str) -> Optional[User]:
    user = db_manager.get_user(username)
    if user:
        user.roles = db_manager.get_user_roles_all_projects(user.id)
        
        all_permissions = set(user.permissions) if hasattr(user, 'permissions') and user.permissions else set()
        
        project_permissions_dict = {}
        
        role_permissions_map = {}
        permission_details_map = {}
        
        all_role_ids = set()
        for role_ids in user.roles.values():
            all_role_ids.update(role_ids)
        
        if all_role_ids:
            db = db_manager.get_db()
            try:
                from sqlalchemy import select
                stmt = select(role_permissions).where(role_permissions.c.role_id.in_(all_role_ids))
                role_permissions_results = db.execute(stmt).fetchall()
                
                for result in role_permissions_results:
                    result_dict = result._asdict() if hasattr(result, '_asdict') else dict(result)
                    role_id = result_dict['role_id']
                    permission_id = result_dict['permission_id']
                    if role_id not in role_permissions_map:
                        role_permissions_map[role_id] = []
                    role_permissions_map[role_id].append(permission_id)
                
                all_permission_ids = set()
                for perm_ids in role_permissions_map.values():
                    all_permission_ids.update(perm_ids)
                
                if all_permission_ids:
                    from app.core.database import Permission
                    permissions_results = db.query(Permission).filter(Permission.id.in_(all_permission_ids)).all()
                    
                    for perm in permissions_results:
                        permission_details_map[perm.id] = {
                            'id': perm.id,
                            'code': perm.code,
                            'name': perm.name,
                            'description': perm.description,
                            'resource_type': perm.resource_type,
                            'action': perm.action
                        }
            finally:
                db.close()
        
        for project_id, role_ids in user.roles.items():
            project_permissions = []
            
            for role_id in role_ids:
                if role_id in role_permissions_map:
                    for perm_id in role_permissions_map[role_id]:
                        if perm_id in permission_details_map:
                            project_permissions.append(permission_details_map[perm_id])
            
            for perm in project_permissions:
                all_permissions.add(perm['code'])
            
            resource_perms = {}
            for perm in project_permissions:
                code = perm['code']
                resource_type = code.split(':')[0]
                if resource_type not in resource_perms:
                    resource_perms[resource_type] = []
                resource_perms[resource_type].append(code)
            
            if resource_perms:
                project_permissions_dict[project_id] = resource_perms
        
        user.permissions = list(all_permissions)
        user.projectPermissions = project_permissions_dict
        
        if not hasattr(user, 'external_credentials'):
            user.external_credentials = {}
        
    return user