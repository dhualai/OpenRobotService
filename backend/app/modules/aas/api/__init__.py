from fastapi import APIRouter

from .auth import router as auth_router
from .users import router as users_router
from .roles import router as roles_router
from .projects import router as projects_router
from .permissions import router as permissions_router

router = APIRouter()

router.include_router(auth_router, prefix="/auth", tags=["auth"])
router.include_router(users_router, prefix="/users", tags=["users"])
router.include_router(roles_router, prefix="/roles", tags=["roles"])
router.include_router(projects_router, prefix="/projects", tags=["projects"])
router.include_router(permissions_router, prefix="/permissions", tags=["permissions"])