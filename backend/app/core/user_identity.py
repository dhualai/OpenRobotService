"""工单身份字段：username 与 users.id 互认。

tasks.assigned_to / created_by 目标语义是 users.id。
过渡期库里、JWT、前端仍可能是 username。所有比较、筛选、写入、通知
都走这里，避免各接口各写一套导致漏单 / 403 / 丢通知。

JWT sub 仍是 username，本模块不改登录签发。
"""
from typing import Any, Dict, Iterable, List, Optional, Set


def lookup_user(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        from app.core.database import db_manager
        return db_manager.get_user(value) or db_manager.get_user_by_id(value)
    except Exception:
        return None


def identity_keys(raw: Optional[str]) -> List[str]:
    """任意 username 或 users.id → [原值, id, username]，去重保序。查不到人则只返回原值。"""
    value = (raw or "").strip()
    if not value:
        return []
    keys = [value]
    user = lookup_user(value)
    if not user:
        return keys
    uid = (user.get("id") or "").strip()
    uname = (user.get("username") or "").strip()
    if uid and uid not in keys:
        keys.append(uid)
    if uname and uname not in keys:
        keys.append(uname)
    return keys


def _attr(user: Any, key: str) -> str:
    if user is None:
        return ""
    if isinstance(user, dict):
        val = user.get(key)
    else:
        val = getattr(user, key, None)
    return (str(val).strip() if val else "")


def actor_username(user: Any) -> str:
    """操作人写入评论/日志仍用 username（评论权限未切 id）。"""
    return _attr(user, "username") or _attr(user, "id") or "system"


def user_keys(user: Any) -> Set[str]:
    """当前登录用户的可比标识（id + username）。不查库。"""
    keys: Set[str] = set()
    for key in ("id", "username"):
        val = _attr(user, key)
        if val:
            keys.add(val)
    return keys


def user_matches(user: Any, *fields: Optional[str]) -> bool:
    """当前用户是否等于工单上的任一身份字段（id / username 都认）。"""
    me = user_keys(user)
    if not me:
        return False
    for field in fields:
        value = (field or "").strip()
        if not value:
            continue
        if value in me:
            return True
        extra = set(identity_keys(value))
        if me & extra:
            return True
    return False


def same_identity(a: Optional[str], b: Optional[str]) -> bool:
    """两个身份字符串是否为同一人。"""
    left = (a or "").strip()
    right = (b or "").strip()
    if not left or not right:
        return False
    if left == right:
        return True
    return bool(set(identity_keys(left)) & set(identity_keys(right)))


def to_user_id(raw: Optional[str]) -> Optional[str]:
    """写入 tasks.assigned_to / created_by：归一成 users.id。查不到人则返回原值。"""
    value = (raw or "").strip()
    if not value:
        return None
    user = lookup_user(value)
    uid = (user.get("id") or "").strip() if user else ""
    return uid or value


def to_username(raw: Optional[str]) -> Optional[str]:
    """发给微信 at.user_names：归一成 username。查不到人则返回原值。"""
    value = (raw or "").strip()
    if not value:
        return None
    user = lookup_user(value)
    uname = (user.get("username") or "").strip() if user else ""
    return uname or value


def to_usernames(values: Optional[Iterable[str]]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for raw in values or []:
        uname = to_username(raw)
        if uname and uname not in seen:
            seen.add(uname)
            out.append(uname)
    return out


def is_admin_user(user: Any) -> bool:
    if isinstance(user, dict):
        if user.get("is_admin"):
            return True
        perms = user.get("permissions") or []
    else:
        if getattr(user, "is_admin", False):
            return True
        perms = getattr(user, "permissions", None) or []
    return "admin" in perms
