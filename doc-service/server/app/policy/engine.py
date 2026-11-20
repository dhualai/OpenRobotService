from __future__ import annotations

from dataclasses import dataclass

ROLES = ("admin", "engineer", "tester", "pm", "pjm", "supervisor", "guest")
STAFF_ROLES = frozenset({"admin", "engineer", "tester", "supervisor"})
DOC_EDITOR_ROLES = frozenset({"admin", "engineer", "tester", "pm", "pjm", "supervisor"})
REQ_EDITOR_ROLES = frozenset({"admin", "engineer", "pm", "pjm", "supervisor"})
REQ_COMMENT_ROLES = frozenset({"admin", "engineer", "tester", "pm", "pjm", "supervisor"})
PRODUCT_H2 = frozenset({"新增内容", "修复内容", "备注"})


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    products: frozenset[str]
    grants: frozenset[tuple[str, str, str]]  # product, kind, resource_id
    name: str = ""
    via: str = "session"  # session | agent


@dataclass(frozen=True)
class ModuleView:
    id: str
    product: str
    encrypted: bool
    admin_protected: bool
    parent_id: str | None
    kind: str
    domain: str | None = None


def _has_product(p: Principal, product: str) -> bool:
    if p.role == "admin":
        return True
    return product in p.products


def _write_all(p: Principal, product: str) -> bool:
    if p.role == "admin":
        return True
    if p.role == "supervisor" and _has_product(p, product):
        return True
    return (product, "product_all", "") in p.grants or (product, "product_all", "*") in p.grants


def _module_write(p: Principal, product: str, module_id: str, domain: str | None) -> bool:
    if _write_all(p, product):
        return True
    if (product, "module", module_id) in p.grants:
        return True
    if domain and (product, "domain", domain) in p.grants:
        return True
    return False


def can_view_product(p: Principal | None, product: str) -> bool:
    if not p:
        return False
    return _has_product(p, product)


def can_browse_module(p: Principal | None, m: ModuleView) -> bool:
    if not p or not _has_product(p, m.product):
        return False
    if p.role == "admin":
        return True
    if _module_write(p, m.product, m.id, m.domain):
        return True
    if m.encrypted:
        return False
    return True


def can_edit_module_content(p: Principal | None, m: ModuleView) -> bool:
    if not p or p.role not in DOC_EDITOR_ROLES:
        return False
    if not _has_product(p, m.product):
        return False
    if p.role == "admin":
        return True
    if p.role in ("pm", "pjm"):
        return False
    return _module_write(p, m.product, m.id, m.domain)


def can_edit_lifecycle(p: Principal | None, m: ModuleView) -> bool:
    return can_edit_module_content(p, m)


def can_edit_board(p: Principal | None, product: str) -> bool:
    """顶层（ALL）拓扑：管理员 / 主管 / 产品 ALL。具体板见 topology.can_edit_board_rel。"""
    if not p:
        return False
    if p.role not in ("admin", "engineer", "tester", "supervisor"):
        return False
    if not _has_product(p, product):
        return False
    return _write_all(p, product)


def can_delete(p: Principal | None) -> bool:
    """Role may delete modules/versions they can edit (protection is checked separately)."""
    if not p:
        return False
    return p.role in ("admin", "engineer", "tester", "supervisor")


def can_protect(p: Principal | None) -> bool:
    return bool(p) and p.role == "admin"


def can_delete_module(p: Principal | None, m: ModuleView) -> bool:
    if not p:
        return False
    if m.kind in ("product_hub", "product_release"):
        return False
    if m.kind == "metrics" and m.id == f"{m.product}_metrics":
        return False
    if m.admin_protected:
        return False
    return can_edit_module_content(p, m)


def can_delete_module_version(p: Principal | None, m: ModuleView, *, version_protected: bool) -> bool:
    if not p:
        return False
    if m.kind in ("product_hub", "product_release"):
        return False
    if m.kind == "metrics" and m.id == f"{m.product}_metrics":
        return False
    if version_protected:
        return False
    return can_edit_module_content(p, m)


def can_mutate_entity(p: Principal | None, m: ModuleView, *, entity_protected: bool = False) -> bool:
    if not p:
        return False
    return can_edit_module_content(p, m) or (
        p.role == "admin" and _has_product(p, m.product)
    )


def can_create_version(p: Principal | None, product: str) -> bool:
    if not p or not _has_product(p, product):
        return False
    return p.role in ("admin", "engineer")


def can_publish_version(p: Principal | None, product: str) -> bool:
    return can_create_version(p, product)


def can_edit_release_covers(p: Principal | None, product: str, is_dev: bool) -> bool:
    if not can_create_version(p, product):
        return False
    if is_dev:
        return True
    return p is not None and p.role == "admin"


def can_edit_release_lifecycle(p: Principal | None, product: str) -> bool:
    if not p or not _has_product(p, product):
        return False
    return p.role in ("admin", "tester")


def can_edit_product_field(p: Principal | None, field: str) -> bool:
    if not p:
        return False
    if p.role == "admin":
        return True
    return field in PRODUCT_H2 and p.role in DOC_EDITOR_ROLES


def can_log_hours(p: Principal | None, product: str) -> bool:
    if not p or p.role not in STAFF_ROLES:
        return False
    return _has_product(p, product)


def can_edit_requirements(p: Principal | None) -> bool:
    return bool(p and p.role in REQ_EDITOR_ROLES)


def can_edit_requirement(p: Principal | None, created_by: str) -> bool:
    if not can_edit_requirements(p) or p is None:
        return False
    if p.role == "admin":
        return True
    return p.username == created_by


def can_delete_requirement(p: Principal | None, created_by: str) -> bool:
    return can_edit_requirement(p, created_by)


def can_link_requirements(p: Principal | None) -> bool:
    return bool(p and p.role in ("engineer", "admin"))


def can_comment_requirement(p: Principal | None) -> bool:
    return bool(p and p.role in REQ_COMMENT_ROLES)


def can_manage_users(p: Principal | None) -> bool:
    return bool(p and p.role == "admin")


def redact_title(p: Principal | None, m: ModuleView, title: str | None) -> str | None:
    if can_browse_module(p, m):
        return title
    return None
