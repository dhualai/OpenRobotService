from app.services.identity_service import IdentityService
from app.services.permission_service import PERM_KNOWLEDGE_CONTRIBUTION


KNOWLEDGE_CONTRIBUTION_PERMISSION_ID = "perm_frontend_admin_knowledge_show"


def ensure_knowledge_contribution_permission() -> None:
    IdentityService.add_permission(
        permission_id=KNOWLEDGE_CONTRIBUTION_PERMISSION_ID,
        code=PERM_KNOWLEDGE_CONTRIBUTION,
        name="显示知识贡献榜",
        resource_type="frontend",
        action="show",
        description="控制后台首页知识贡献榜标题、卡片及查看榜单入口，按角色明确授权；不改变知识内容或匿名审核权限",
    )
