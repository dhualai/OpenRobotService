from app.services.identity_service import IdentityService
from app.services.permission_service import PERM_KB_WRITE


KB_PORTAL_PERMISSION_ID = "perm_frontend_kb_write"


def ensure_kb_portal_permission() -> None:
    """确保智库写权限码已注册到 permissions 表（幂等，已存在自动跳过）。

    与公告、知识贡献榜同一范式：**只在** permissions.get_all_permissions 的注册块里调用。
    切勿在接口函数体内调用——require_permission 是 FastAPI 依赖，先于函数体执行，
    没权限的账号会先被 403 挡住，永远走不到注册那行（生产上就这样自锁过）。
    """
    IdentityService.add_permission(
        permission_id=KB_PORTAL_PERMISSION_ID,
        code=PERM_KB_WRITE,
        name="智库编辑",
        resource_type="frontend",
        action="write",
        description=(
            "智库（/t/app/kb/）写权限：有本码者在智库内为 engineer 可编辑；"
            "admin 账号直通；其他已登录用户为 guest 只读"
        ),
    )
