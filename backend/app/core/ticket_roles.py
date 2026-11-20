"""工单角色统一解析（单一出口）—— 代他人提单改造的核心收敛点。

需求方案：`docs/PRODUCT/代他人提单（代理提单）功能设计方案.md` §3.4

**为什么需要本模块**：改造前身份判定分散在前后端约 26 处（`user_matches(...)` /
`same_identity(...)` / 前端 `isSameUser` 各自拼），被代理人的参与权无法在这些点
逐一保证。本模块把所有判定收敛为**一个出口**，端点只读 `TicketRoles`，不再自己拼。

语义（与设计文档 §3.4 对齐）：
- `created_by` 语义**保持不变** = 代理人（全程不变）；
- 被代理人权限由 `task_proxy_relation` 派生：
  - `pending`   → 只读（可查看、可评论，不可改状态、不参与协商）
  - `acknowledged` → 协办（可协商、可催办、可确认关闭）
  - `declined`  → 无额外权限
- **side（回合协商侧别）**：被代理人归 `creator` 侧（与代理人同侧，代表问题方），
  且**仅 acknowledged 时生效**。

安全：本模块只做**已鉴权身份 → 角色**的映射，不做数据返回，天然不泄露他人身份。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from app.core.user_identity import is_admin_user, same_identity, user_matches

# 侧别标识（与 api/task.py 的 _ACTOR_SIDE_* 保持一致）
SIDE_ASSIGNED = "assigned"
SIDE_CREATOR = "creator"

# 关系状态（避免为常量反向依赖 models 包）
RELATION_PENDING = "pending"
RELATION_ACKNOWLEDGED = "acknowledged"
RELATION_DECLINED = "declined"


@dataclass
class TicketRoles:
    """当前登录用户在**某一张工单**上的角色集合。

    所有端点只读本对象，不再各自拼 `user_matches`。
    """

    # --- 基础身份 ---
    is_creator: bool = False      # tasks.created_by（= 代理人，全程不变）
    is_assignee: bool = False     # tasks.assigned_to
    is_admin: bool = False

    # --- 代理关系派生 ---
    is_agent: bool = False             # 我是本单的代理人
    is_principal: bool = False         # 我是被代理人且已确认跟进（协办权）
    is_pending_principal: bool = False  # 我是被代理人但尚未确认（只读态）

    # --- 展示用（不参与权限） ---
    is_customer: bool = False          # tasks.customer，仅展示「联系人」
    is_follower: bool = False          # 已关注（不影响权限，供前端星标）

    # --- 派生结论 ---
    is_related: bool = False           # 与工单相关（任一角色命中）→ 列表「与我相关」
    can_operate: bool = False          # 可推进/改状态（协办及以上）
    can_close: bool = False            # 可确认关闭（已解决 → 已关闭）
    can_withdraw: bool = False         # 可撤回（仅 created_by）
    side: Optional[str] = None         # 'assigned' | 'creator'；principal 归 creator
    relation_status: Optional[str] = None  # 本单代理关系状态（无关系为 None）


def _relation_of(relation: Any) -> Optional[str]:
    """从 ORM 对象 / dict 中取关系状态。"""
    if relation is None:
        return None
    if isinstance(relation, dict):
        status = relation.get("relation_status")
    else:
        status = getattr(relation, "relation_status", None)
    status = (status or "").strip() if isinstance(status, str) else status
    return status or None


def _relation_agent(relation: Any) -> tuple:
    """取关系里的代理人标识对（id, username）。"""
    if relation is None:
        return ("", "")
    if isinstance(relation, dict):
        return (relation.get("agent_id") or "", relation.get("agent_username") or "")
    return (
        getattr(relation, "agent_id", None) or "",
        getattr(relation, "agent_username", None) or "",
    )


def _relation_principal(relation: Any) -> tuple:
    """取关系里的被代理人标识对（id, username）。"""
    if relation is None:
        return ("", "")
    if isinstance(relation, dict):
        return (relation.get("principal_id") or "", relation.get("principal_username") or "")
    return (
        getattr(relation, "principal_id", None) or "",
        getattr(relation, "principal_username", None) or "",
    )


def resolve_ticket_roles(
    ticket: Any,
    current_user: Any,
    relation: Any = None,
    *,
    is_follower: bool = False,
    can_operate: Optional[bool] = None,
) -> TicketRoles:
    """解析当前用户在工单上的角色集合（**唯一出口**）。

    Args:
        ticket: `Task` ORM 实例（需有 created_by / assigned_to / customer）。
        current_user: 已鉴权用户（dict 或 ORM），需含 id / username / is_admin / permissions。
        relation: 该工单的 `TaskProxyRelation`（可为 None；列表场景由批量 map 传入）。
        is_follower: 是否已关注（调用方查得，本模块不查库）。
        can_operate: 是否具备 `backend:tasks:operate` 权限；None 时由 current_user.permissions 推断。

    Returns:
        TicketRoles
    """
    roles = TicketRoles()
    if ticket is None or current_user is None:
        return roles

    created_by = getattr(ticket, "created_by", None)
    assigned_to = getattr(ticket, "assigned_to", None)
    customer = getattr(ticket, "customer", None)

    roles.is_creator = user_matches(current_user, created_by)
    roles.is_assignee = user_matches(current_user, assigned_to)
    roles.is_customer = user_matches(current_user, customer)
    roles.is_admin = is_admin_user(current_user)
    roles.is_follower = bool(is_follower)

    # 权限位：显式传入优先，否则从 user.permissions 推断
    if can_operate is None:
        perms = (
            current_user.get("permissions") or []
            if isinstance(current_user, dict)
            else (getattr(current_user, "permissions", None) or [])
        )
        can_operate = "backend:tasks:operate" in perms or roles.is_admin
    roles.can_operate = bool(can_operate)

    # --- 代理关系派生 ---
    status = _relation_of(relation)
    roles.relation_status = status
    if status:
        agent_id, agent_username = _relation_agent(relation)
        principal_id, principal_username = _relation_principal(relation)

        is_agent_user = bool(agent_id) and user_matches(current_user, agent_id, agent_username)
        is_principal_user = bool(principal_id) and user_matches(
            current_user, principal_id, principal_username
        )

        roles.is_agent = is_agent_user
        if is_principal_user:
            if status == RELATION_ACKNOWLEDGED:
                roles.is_principal = True
            elif status == RELATION_PENDING:
                roles.is_pending_principal = True
            # declined：无额外权限

    # --- 派生结论 ---
    roles.is_related = bool(
        roles.is_creator or roles.is_assignee or roles.is_agent
        or roles.is_principal or roles.is_pending_principal or roles.is_customer
    )

    # 可推进/改状态：接单人、代理人（created_by 本就含）、已确认的被代理人、管理员、有 operate 权限
    roles.can_operate = bool(
        roles.can_operate
        or roles.is_assignee
        or roles.is_creator
        or roles.is_principal
        or roles.is_admin
    )

    # 确认关闭：代理人（created_by）+ 已确认被代理人 + 管理员
    # 注意：customer 不再参与（设计文档决策 6，修掉「新单 customer 为空导致非 admin 关不掉单」）
    roles.can_close = bool(roles.is_creator or roles.is_principal or roles.is_admin)

    # 撤回：仅实际提交人（代理人），被代理人**不可撤回**（决策 4）
    roles.can_withdraw = bool(roles.is_creator)

    # --- side：回合协商侧别 ---
    if roles.is_assignee:
        roles.side = SIDE_ASSIGNED
    elif roles.is_creator or roles.is_agent or roles.is_principal:
        roles.side = SIDE_CREATOR
    else:
        roles.side = None

    return roles


def resolve_roles_batch(
    tickets: Iterable[Any],
    current_user: Any,
    relations_map: Optional[Dict[int, Any]] = None,
    *,
    follower_ids: Optional[Iterable[int]] = None,
    can_operate: Optional[bool] = None,
) -> Dict[int, TicketRoles]:
    """列表场景批量解析（避免 N+1：关系由调用方一次 IN 查询后传入）。

    Args:
        tickets: Task 实例列表。
        relations_map: {task_id: TaskProxyRelation}，见 ProxyRelationService.get_relations_map。
        follower_ids: 当前用户已关注的 task_id 集合。
    """
    relations_map = relations_map or {}
    follower_set = set(follower_ids or [])
    out: Dict[int, TicketRoles] = {}
    for ticket in tickets or []:
        tid = getattr(ticket, "id", None)
        if tid is None:
            continue
        out[tid] = resolve_ticket_roles(
            ticket,
            current_user,
            relations_map.get(tid),
            is_follower=tid in follower_set,
            can_operate=can_operate,
        )
    return out


async def load_relation(db: Any, task_id: Optional[int]) -> Optional[Any]:
    """按 task_id 读取代理关系（无关系 / 异常均返回 None，不阻断主流程）。

    放在本模块以便各调用方复用，避免为查关系而在模块间互相导入造成循环依赖。
    """
    if not task_id:
        return None
    try:
        from app.modules.tasks.services.proxy_relation_service import ProxyRelationService

        return await ProxyRelationService.get_task_relation(db, task_id)
    except Exception as e:  # pragma: no cover - 兜底，避免关系查询拖垮主流程
        import logging

        logging.getLogger(__name__).warning(f"读取代理关系失败 task_id={task_id}: {e}")
        return None


async def get_ticket_roles(
    db: Any,
    ticket: Any,
    current_user: Any,
    *,
    is_follower: bool = False,
    can_operate: Optional[bool] = None,
) -> Optional[TicketRoles]:
    """**统一角色解析入口**（各 API 模块的唯一出口）。

    组合「查关系 + 解析角色」两步，供 `api/task.py`、`api/spec_doc.py`、
    `call/api/my_tasks.py` 等共用，避免各模块自行查表或重复实现判定。
    """
    if ticket is None:
        return None
    relation = await load_relation(db, getattr(ticket, "id", None))
    return resolve_ticket_roles(
        ticket,
        current_user,
        relation,
        is_follower=is_follower,
        can_operate=can_operate,
    )
