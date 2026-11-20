"""「责任模块树」编辑审批单 业务服务层。

权限规则（对某个功能模块的修改）：
  - 管理员 admin / 拥有 backend:module-tree:write 的人 → 免审批直改
  - 当前用户是该功能负责人（engineers 含其 id）→ 免审批直改
  - 该功能待分配（engineers 空）→ 任意登录用户免审批直改
  - 已被他人负责 → 需创建审批单，原负责人同意后生效
"""
import json
import logging
from typing import Dict, Any, List, Optional

from app.core.database import db_manager
from app.models.module_tree_edit import ModuleTreeEdit

logger = logging.getLogger(__name__)

SPECIAL_PERM = "backend:module-tree:write"


def _user_id(user: Dict[str, Any]) -> str:
    return str(user.get("id") or user.get("user_id") or "")


def _user_name(user: Dict[str, Any]) -> str:
    return str(user.get("name") or user.get("username") or "")


def is_admin_or_special(user: Dict[str, Any]) -> bool:
    """管理员或有模块树特殊写权限 → True。"""
    perms = user.get("permissions") or []
    if isinstance(perms, (list, set)):
        if "admin" in perms or SPECIAL_PERM in perms:
            return True
        for p in perms:
            if p == SPECIAL_PERM or p == f"{SPECIAL_PERM}:*" or p == "*":
                return True
    roles = user.get("roles") or {}
    if isinstance(roles, dict):
        for rp in roles.values():
            if isinstance(rp, (list, set)) and ("admin" in rp or SPECIAL_PERM in rp):
                return True
    return False


def can_direct_edit(user: Dict[str, Any], engineers: Optional[List[str]]) -> bool:
    """是否可直接修改该功能（无需审批）。"""
    if is_admin_or_special(user):
        return True
    engs = engineers or []
    if not engs:
        return True  # 待分配：任意登录用户可直改
    return _user_id(user) in [str(e) for e in engs]


def _get_db():
    return db_manager.get_db()


def create_edit(
    product: str,
    iface_key: str,
    func_key: str,
    old_json: Optional[Dict[str, Any]],
    new_json: Optional[Dict[str, Any]],
    requester: Dict[str, Any],
    owner_ids: List[str],
) -> Optional[int]:
    """创建一条审批单，返回其 id。"""
    db = _get_db()
    try:
        row = ModuleTreeEdit(
            product=product,
            iface_key=iface_key,
            func_key=func_key,
            old_json=old_json,
            new_json=new_json,
            requester_id=_user_id(requester),
            requester_name=_user_name(requester),
            owner_ids=owner_ids or [],
            status="pending",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id
    except Exception:
        db.rollback()
        logger.exception("创建模块树审批单失败")
        return None
    finally:
        db.close()


def list_edits(status: str = "pending", user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出审批单。按 status 过滤；user_id 给定时只返回其作为负责人（owner）或发起人的单。"""
    db = _get_db()
    try:
        q = db.query(ModuleTreeEdit)
        if status:
            q = q.filter(ModuleTreeEdit.status == status)
        rows = q.order_by(ModuleTreeEdit.created_at.desc()).all()
        result = []
        for r in rows:
            owners = r.owner_ids or []
            is_owner = user_id and user_id in [str(o) for o in owners]
            is_requester = user_id and str(r.requester_id) == str(user_id)
            # 无 user_id 或属于该用户相关的单，才返回（避免他人待办）
            if user_id is None or is_owner or (r.status != "pending" and is_requester):
                result.append(_row_to_dict(r))
        return result
    finally:
        db.close()


def _row_to_dict(r: ModuleTreeEdit) -> Dict[str, Any]:
    return {
        "id": r.id,
        "product": r.product,
        "iface_key": r.iface_key,
        "func_key": r.func_key,
        "old": r.old_json,
        "new": r.new_json,
        "requester_id": r.requester_id,
        "requester_name": r.requester_name,
        "owner_ids": r.owner_ids or [],
        "status": r.status,
        "decider_id": r.decider_id,
        "decision_note": r.decision_note,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
    }


def decide_edit(edit_id: int, action: str, decider: Dict[str, Any], note: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """批准 / 驳回审批单。返回审批后的单 dict，或 None（不可审批/不存在）。"""
    from datetime import datetime
    from .module_tree_service import apply_function_change
    db = _get_db()
    try:
        row = db.query(ModuleTreeEdit).filter(ModuleTreeEdit.id == edit_id).first()
        if not row or row.status != "pending":
            return None
        # 只有「负责人的一员」或管理员/特殊权限才能审批
        owners = [str(o) for o in (row.owner_ids or [])]
        if not (is_admin_or_special(decider) or _user_id(decider) in owners):
            return None
        row.status = "approved" if action == "approve" else "rejected"
        row.decider_id = _user_id(decider)
        row.decision_note = note
        row.decided_at = datetime.utcnow()
        db.commit()
        result = _row_to_dict(row)
        if action == "approve" and row.new_json:
            # 应用新值到 DB（覆盖该功能节点）
            applied = apply_function_change(row.product, row.iface_key, row.func_key, row.new_json)
            result["applied"] = applied
        return result
    except Exception:
        db.rollback()
        logger.exception("审批模块树编辑失败")
        return None
    finally:
        db.close()
