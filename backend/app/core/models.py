from typing import Dict, List, Optional, Any
import time

class User:
    def __init__(self, id: str, username: str, hashed_password: str, permissions: List[str], roles: Optional[Dict[str, List[str]]] = None, 
                 projectPermissions: Optional[Dict[str, Dict[str, List[str]]]] = None, name: Optional[str] = None, status: str = "inactive",
                 external_credentials: Optional[Dict[str, Dict[str, str]]] = None):
        self.id = id
        self.username = username
        self.hashed_password = hashed_password
        self.permissions = permissions
        self.projectPermissions = projectPermissions or {}
        self.roles = roles or {}
        self.name = name
        self.status = status
        self.external_credentials = external_credentials or {}

class Token:
    def __init__(self, access_token: str, token_type: str = "bearer"):
        self.access_token = access_token
        self.token_type = token_type

class TokenData:
    def __init__(self, username: Optional[str] = None):
        self.username = username

class Role:
    def __init__(self, id: str, name: str):
        self.id = id
        self.name = name

class Project:
    def __init__(self, id: str, name: str):
        self.id = id
        self.name = name

class RoleAssignment:
    def __init__(self, project_id: str, role_id: str):
        self.project_id = project_id
        self.role_id = role_id