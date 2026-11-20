from __future__ import annotations

"""账号统一通道：把摇人吧的登录态接进来。

本服务不再有本地账密登录（``POST /api/v1/auth/login`` 已关闭）。
请求带 ``Authorization: Bearer <摇人吧 access_token>`` 时，由本模块服务端到服务端
调摇人吧 ``GET {ORS_BASE_URL}/api/auth/me`` 校验，再按下面的规则落到本地 User/Grant：

* 库里已有同 username 的用户行 → **原样沿用**其 role 与 Grant（老用户无感），
  但允许**提权**：历史 role 是只读（guest）而摇人吧侧已授予 ``frontend:kb:write`` 时，
  升级为 engineer 并把 ``product_all`` 授权从 read 提升到 write
  （否则「先登录、后加权限码」的用户会永远停在 guest——线上踩过）；
* 没有历史行 → 按摇人吧权限码映射建档：
  - ``admin``（含 username=admin）→ role=admin，两产品 product_all + write
  - ``frontend:kb:write`` / ``frontend:kb:*`` → role=engineer，两产品 product_all + write
  - 其他已登录用户 → role=guest，两产品 product_all + read（只读）
"""

import logging
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from app.infra.models import Grant, User, UserProduct
from app.policy import Principal
from app.services import auth as auth_svc
from app.settings import get_settings

log = logging.getLogger("topo.ors_auth")

PRODUCTS = ("usp", "ors")
_VERIFY_TIMEOUT = 5.0
KB_WRITE_PERMISSION = "frontend:kb:write"


@dataclass(frozen=True)
class OrsUser:
    username: str
    name: str
    permissions: tuple[str, ...]


def _perm_matches(user_perm: str, required: str) -> bool:
    """用户权限串是否覆盖所需权限：按冒号分段，段数须一致，
    **用户串中的 ``*`` 段匹配任意段**（与摇人吧 ``hasPermission`` 同口径）。"""
    got = [s for s in (user_perm or "").split(":") if s]
    want = [s for s in required.split(":") if s]
    if not got or len(got) != len(want):
        return False
    return all(g == "*" or g == w for g, w in zip(got, want))


def _perm_matches_any(permissions: tuple[str, ...], required: str) -> bool:
    perms = [str(p) for p in (permissions or ())]
    if "*" in perms:  # 与摇人吧一致：通配权限直达
        return True
    return any(_perm_matches(p, required) for p in perms)


def is_ors_admin(permissions: tuple[str, ...]) -> bool:
    return _perm_matches_any(permissions, "admin")


def has_kb_write(permissions: tuple[str, ...]) -> bool:
    return _perm_matches_any(permissions, KB_WRITE_PERMISSION)


def verify_ors_token(token: str) -> OrsUser | None:
    """调摇人吧校验 token。任何失败（网络/超时/非 200/脏响应）都返回 None，不向外抛。"""
    if not token:
        return None
    url = get_settings().ors_base_url.rstrip("/") + "/api/auth/me"
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=_VERIFY_TIMEOUT,
        )
    except Exception as exc:  # 网络不通/超时一律按未登录处理
        log.warning("ors auth verify failed: %s", exc.__class__.__name__)
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    username = str(data.get("username") or "").strip()
    if not username:
        return None
    perms = data.get("permissions")
    if not isinstance(perms, list):
        perms = []
    return OrsUser(
        username=username,
        name=str(data.get("name") or "").strip(),
        permissions=tuple(str(p) for p in perms),
    )


def role_for(permissions: tuple[str, ...]) -> str:
    if is_ors_admin(permissions):
        return "admin"
    if has_kb_write(permissions):
        return "engineer"
    return "guest"


def _set_product_perm(db: Session, username: str, product: str, perm: str) -> None:
    """把某产品的 ``product_all`` 授权置为指定权限（不存在则建）。"""
    row = (
        db.query(Grant)
        .filter(
            Grant.username == username,
            Grant.product == product,
            Grant.kind == "product_all",
            Grant.resource_id == "",
        )
        .one_or_none()
    )
    if row is None:
        db.add(
            Grant(
                username=username,
                product=product,
                kind="product_all",
                resource_id="",
                perm=perm,
            )
        )
        return
    row.perm = perm


def _upgrade_readonly(db: Session, row: User, user: OrsUser) -> None:
    """只读账号在获得写权限码后提权。

    A 方案要求老用户不被降级，因此只在「历史是只读、目标可写」时动：
    已有可写角色（engineer/supervisor/admin）一律原样保留。
    """
    if row.role != "guest":
        return
    target = role_for(user.permissions)
    if target == "guest":
        return
    log.info("upgrade readonly user %s: guest -> %s", row.username, target)
    row.role = target
    for product in PRODUCTS:
        _set_product_perm(db, row.username, product, "write")


def provision(db: Session, user: OrsUser) -> User:
    """确保本地有该用户。已有行沿用 role/Grant（只读账号可被权限码提权），新行按权限码映射建档。"""
    row = db.get(User, user.username)
    if row:
        if user.name and not (row.display_name or "").strip():
            row.display_name = user.name
        _upgrade_readonly(db, row, user)
        return row
    role = role_for(user.permissions)
    row = User(
        username=user.username,
        password_hash="",
        role=role,
        display_name=user.name or user.username,
    )
    db.add(row)
    db.flush()
    for product in PRODUCTS:
        db.add(UserProduct(username=user.username, product=product))
        db.add(
            Grant(
                username=user.username,
                product=product,
                kind="product_all",
                resource_id="",
                perm="write" if role != "guest" else "read",
            )
        )
    return row


def principal_from_ors(db: Session, bearer: str | None) -> Principal | None:
    """从 ``Authorization: Bearer <token>`` 解析出本地 Principal；失败返回 None。"""
    if not bearer or not bearer.startswith("Bearer "):
        return None
    token = bearer.removeprefix("Bearer ").strip()
    if not token:
        return None
    user = verify_ors_token(token)
    if not user:
        return None
    provision(db, user)
    return auth_svc.load_principal(db, user.username, via="ors")
