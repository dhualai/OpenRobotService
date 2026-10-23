"""责任双树服务层：两棵树（org / skill）的节点与人挂载读写。

双树试做页的树此前只存在 localStorage + `.local` JSON 快照里，加载时把多份副本按
「最富的当 base」互相合并，2026-10-15、10-16 各丢过一次组织树。本服务是三张表
（`app/models/resp_tree.py`）的唯一读写入口，DB 即真相源。

并发：每棵树有 `revision` 乐观锁。带 `expected_revision` 的写操作若与库中不符，
抛 `RevisionConflict`（接口层转 409），前端据此刷新而不是静默覆盖。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resp_tree import (
    LINK_ROLES,
    LINK_SOURCES,
    LINK_TYPES,
    NODE_KINDS,
    RespNode,
    RespNodeLink,
    RespTree,
)

# 两棵树的默认名（首次初始化用）
DEFAULT_TREES = {"skill": "技能·六域", "org": "公司组织"}

ORG_KINDS = ("company", "department")


class RevisionConflict(Exception):
    """乐观锁冲突：调用方拿的 revision 已过期（接口层转 409）。"""

    def __init__(self, expected: int, actual: int):
        super().__init__(f"树已被他人修改：期望 revision={expected}，实际 {actual}")
        self.expected = expected
        self.actual = actual


# ── 内部工具 ────────────────────────────────────────────────

def _norm(value: Any, allowed: Iterable[str], default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _node_out(node: RespNode) -> Dict[str, Any]:
    return {
        "id": node.id,
        "tree_id": node.tree_id,
        "parent_id": node.parent_id,
        "name": node.name,
        "description": node.description or "",
        "keywords": node.keywords or [],
        "anchor": node.anchor or "",
        "owners": node.owners or [],
        "linked_node_id": node.linked_node_id,
        "node_kind": node.node_kind,
        "org_kind": node.org_kind,
        "vehicle_models": node.vehicle_models or "",
        "sort_order": node.sort_order,
    }


def _link_out(link: RespNodeLink) -> Dict[str, Any]:
    return {
        "user_id": link.user_id,
        "node_id": link.node_id,
        "link_type": link.link_type,
        "role": link.role,
        "source": link.source,
    }


async def _get_tree(db: AsyncSession, tree_id: int) -> RespTree:
    tree = await db.get(RespTree, tree_id)
    if tree is None:
        raise LookupError(f"树不存在: id={tree_id}")
    return tree


async def _check_revision(db: AsyncSession, tree: RespTree, expected: Optional[int]) -> None:
    """expected 为 None 表示调用方不做并发检查（只读语义或兼容旧调用）。"""
    if expected is None:
        return
    await db.refresh(tree, ["revision"])
    if int(tree.revision or 0) != int(expected):
        raise RevisionConflict(int(expected), int(tree.revision or 0))


async def _bump(db: AsyncSession, tree_id: int) -> int:
    """该树任何写操作后 revision +1，返回新值。"""
    tree = await _get_tree(db, tree_id)
    tree.revision = int(tree.revision or 0) + 1
    return tree.revision


async def _descendant_ids(db: AsyncSession, root_id: int) -> List[int]:
    """含自身的整棵子树 id（只看未软删的）。"""
    rows = (await db.execute(
        select(RespNode.id, RespNode.parent_id).where(RespNode.is_deleted.is_(False))
    )).all()
    children: Dict[Optional[int], List[int]] = {}
    for node_id, parent_id in rows:
        children.setdefault(parent_id, []).append(node_id)

    out: List[int] = []
    stack = [root_id]
    while stack:
        cur = stack.pop()
        out.append(cur)
        stack.extend(children.get(cur, []))
    return out


async def _next_sort_order(db: AsyncSession, tree_id: int, parent_id: Optional[int]) -> int:
    value = (await db.execute(
        select(func.max(RespNode.sort_order)).where(
            RespNode.tree_id == tree_id,
            RespNode.parent_id.is_(None) if parent_id is None else RespNode.parent_id == parent_id,
            RespNode.is_deleted.is_(False),
        )
    )).scalar()
    return int(value or 0) + 1


# ── 树 ──────────────────────────────────────────────────────

async def ensure_trees(db: AsyncSession) -> List[RespTree]:
    """确保 org / skill 两棵树存在（空库初始化用），返回现有全部树。"""
    existing = (await db.execute(select(RespTree))).scalars().all()
    have = {t.kind for t in existing}
    created = False
    for kind, name in DEFAULT_TREES.items():
        if kind not in have:
            db.add(RespTree(kind=kind, name=name))
            created = True
    if created:
        await db.commit()
        existing = (await db.execute(select(RespTree))).scalars().all()
    return list(existing)


async def list_trees(db: AsyncSession, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    stmt = select(RespTree).order_by(RespTree.kind, RespTree.id)
    if kind:
        stmt = stmt.where(RespTree.kind == kind)
    trees = (await db.execute(stmt)).scalars().all()
    return [
        {"id": t.id, "kind": t.kind, "name": t.name, "status": t.status,
         "revision": int(t.revision or 0)}
        for t in trees
    ]


async def get_tree_payload(db: AsyncSession, tree_id: int) -> Dict[str, Any]:
    """整树 + 挂载一次取回：试做页只读这一次。"""
    tree = await _get_tree(db, tree_id)
    nodes = (await db.execute(
        select(RespNode).where(RespNode.tree_id == tree_id, RespNode.is_deleted.is_(False))
        .order_by(RespNode.sort_order, RespNode.id)
    )).scalars().all()
    node_ids = [n.id for n in nodes]
    links: List[RespNodeLink] = []
    if node_ids:
        links = (await db.execute(
            select(RespNodeLink).where(RespNodeLink.node_id.in_(node_ids))
        )).scalars().all()
    return {
        "tree": {"id": tree.id, "kind": tree.kind, "name": tree.name,
                 "status": tree.status, "revision": int(tree.revision or 0)},
        "nodes": [_node_out(n) for n in nodes],
        "links": [_link_out(x) for x in links],
    }


# ── 节点 ────────────────────────────────────────────────────

async def create_node(db: AsyncSession, payload: Dict[str, Any],
                      expected_revision: Optional[int] = None) -> Dict[str, Any]:
    tree_id = int(payload.get("tree_id") or 0)
    name = str(payload.get("name") or "").strip()
    if not tree_id or not name:
        raise ValueError("tree_id 与 name 必填")
    tree = await _get_tree(db, tree_id)
    await _check_revision(db, tree, expected_revision)

    parent_id = payload.get("parent_id")
    parent_id = int(parent_id) if parent_id not in (None, "", 0) else None
    if parent_id is not None:
        parent = await db.get(RespNode, parent_id)
        if parent is None or parent.is_deleted or parent.tree_id != tree_id:
            raise ValueError(f"父节点不存在或不属于本树: {parent_id}")

    node = RespNode(
        tree_id=tree_id,
        parent_id=parent_id,
        name=name,
        description=str(payload.get("description") or ""),
        keywords=payload.get("keywords") or [],
        anchor=str(payload.get("anchor") or ""),
        owners=payload.get("owners") or [],
        linked_node_id=payload.get("linked_node_id") or None,
        node_kind=_norm(payload.get("node_kind"), NODE_KINDS, "leaf"),
        org_kind=_norm(payload.get("org_kind"), ORG_KINDS, "") or None,
        vehicle_models=str(payload.get("vehicle_models") or ""),
        sort_order=int(payload.get("sort_order")
                       or await _next_sort_order(db, tree_id, parent_id)),
    )
    db.add(node)
    await db.flush()
    revision = await _bump(db, tree_id)
    await db.commit()
    await db.refresh(node)
    return {"node": _node_out(node), "revision": revision}


async def patch_node(db: AsyncSession, node_id: int, patch: Dict[str, Any],
                     expected_revision: Optional[int] = None) -> Dict[str, Any]:
    """改名 / 描述 / 换父（dock）。换父时服务端做环检测。"""
    node = await db.get(RespNode, node_id)
    if node is None or node.is_deleted:
        raise LookupError(f"节点不存在: id={node_id}")
    tree = await _get_tree(db, node.tree_id)
    await _check_revision(db, tree, expected_revision)

    if "parent_id" in patch:
        raw = patch.get("parent_id")
        new_parent = int(raw) if raw not in (None, "", 0) else None
        if new_parent == node.id:
            raise ValueError("不能把节点挂到自己下面")
        if new_parent is not None:
            parent = await db.get(RespNode, new_parent)
            if parent is None or parent.is_deleted or parent.tree_id != node.tree_id:
                raise ValueError(f"目标父节点不存在或不属于本树: {new_parent}")
            if new_parent in set(await _descendant_ids(db, node.id)) - {node.id}:
                raise ValueError("不能把节点挂到自己的子孙下面（会成环）")
        node.parent_id = new_parent

    for field in ("name", "description", "anchor", "vehicle_models"):
        if field in patch:
            value = str(patch.get(field) or "").strip() if field == "name" else str(patch.get(field) or "")
            if field == "name" and not value:
                raise ValueError("名称不能为空")
            setattr(node, field, value)
    for field in ("keywords", "owners", "linked_node_id", "sort_order"):
        if field in patch:
            setattr(node, field, patch.get(field))
    if "node_kind" in patch:
        node.node_kind = _norm(patch.get("node_kind"), NODE_KINDS, node.node_kind)
    if "org_kind" in patch:
        node.org_kind = _norm(patch.get("org_kind"), ORG_KINDS, "") or None

    revision = await _bump(db, node.tree_id)
    await db.commit()
    await db.refresh(node)
    return {"node": _node_out(node), "revision": revision}


async def delete_node(db: AsyncSession, node_id: int,
                      expected_revision: Optional[int] = None) -> Dict[str, Any]:
    """递归软删整棵子树，并硬删这些节点上的人挂载。"""
    node = await db.get(RespNode, node_id)
    if node is None or node.is_deleted:
        raise LookupError(f"节点不存在: id={node_id}")
    tree = await _get_tree(db, node.tree_id)
    await _check_revision(db, tree, expected_revision)

    ids = await _descendant_ids(db, node_id)
    now = func.now()
    await db.execute(
        RespNode.__table__.update()
        .where(RespNode.id.in_(ids))
        .values(is_deleted=True, deleted_at=now)
    )
    # 挂载硬删：软删会顶住 (user_id, node_id) 唯一键，挡掉「删了再挂回同一个人」
    await db.execute(delete(RespNodeLink).where(RespNodeLink.node_id.in_(ids)))
    revision = await _bump(db, node.tree_id)
    await db.commit()
    return {"deleted": ids, "revision": revision}


async def set_owners(db: AsyncSession, node_id: int, owners: List[str],
                     expected_revision: Optional[int] = None) -> Dict[str, Any]:
    node = await db.get(RespNode, node_id)
    if node is None or node.is_deleted:
        raise LookupError(f"节点不存在: id={node_id}")
    tree = await _get_tree(db, node.tree_id)
    await _check_revision(db, tree, expected_revision)

    node.owners = [str(x) for x in (owners or []) if str(x).strip()]
    revision = await _bump(db, node.tree_id)
    await db.commit()
    await db.refresh(node)
    return {"node": _node_out(node), "revision": revision}


async def batch_create_nodes(db: AsyncSession, tree_id: int, nodes: List[Dict[str, Any]],
                             expected_revision: Optional[int] = None) -> Dict[str, Any]:
    """按 (parent_id, name) 自然键幂等补节点：已存在就跳过，只补缺的。

    「把系统公司部门填进组织树」用它 —— 可重复点，不会造出重名节点。
    """
    tree = await _get_tree(db, tree_id)
    await _check_revision(db, tree, expected_revision)

    existing = (await db.execute(
        select(RespNode).where(RespNode.tree_id == tree_id, RespNode.is_deleted.is_(False))
    )).scalars().all()
    seen = {(n.parent_id, n.name) for n in existing}

    created: List[Dict[str, Any]] = []
    for item in nodes or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        parent_id = item.get("parent_id")
        parent_id = int(parent_id) if parent_id not in (None, "", 0) else None
        if (parent_id, name) in seen:
            continue
        node = RespNode(
            tree_id=tree_id,
            parent_id=parent_id,
            name=name,
            description=str(item.get("description") or ""),
            vehicle_models=str(item.get("vehicle_models") or ""),
            node_kind=_norm(item.get("node_kind"), NODE_KINDS, "org"),
            org_kind=_norm(item.get("org_kind"), ORG_KINDS, "") or None,
            sort_order=int(item.get("sort_order")
                           or await _next_sort_order(db, tree_id, parent_id)),
        )
        db.add(node)
        await db.flush()
        seen.add((parent_id, name))
        created.append(_node_out(node))

    revision = await _bump(db, tree_id) if created else int(tree.revision or 0)
    await db.commit()
    return {"created": created, "revision": revision}


async def replace_tree(db: AsyncSession, tree_id: int,
                       nodes: List[Dict[str, Any]], links: List[Dict[str, Any]],
                       expected_revision: Optional[int] = None) -> Dict[str, Any]:
    """整树替换（「导入 JSON」用）：事务内清空该树再按入参重建。

    注意：会丢掉入参里没有的节点，只用于用户显式导入整棵树。
    """
    tree = await _get_tree(db, tree_id)
    await _check_revision(db, tree, expected_revision)

    await db.execute(delete(RespNodeLink).where(RespNodeLink.node_id.in_(
        select(RespNode.id).where(RespNode.tree_id == tree_id))))
    await db.execute(delete(RespNode).where(RespNode.tree_id == tree_id))
    await db.flush()

    # 入参里的父子用调用方的临时 id 表示，落库时映射成真实自增 id
    id_map: Dict[str, int] = {}
    for item in nodes or []:
        node = RespNode(
            tree_id=tree_id,
            parent_id=None,
            name=str(item.get("name") or "").strip() or "未命名",
            description=str(item.get("description") or ""),
            keywords=item.get("keywords") or [],
            anchor=str(item.get("anchor") or ""),
            owners=item.get("owners") or [],
            node_kind=_norm(item.get("node_kind"), NODE_KINDS, "leaf"),
            org_kind=_norm(item.get("org_kind"), ORG_KINDS, "") or None,
            vehicle_models=str(item.get("vehicle_models") or ""),
            sort_order=int(item.get("sort_order") or 0),
            legacy_ref=item.get("legacy_ref") or None,
        )
        db.add(node)
        await db.flush()
        old_id = item.get("id")
        if old_id not in (None, ""):
            id_map[str(old_id)] = node.id
        if item.get("linked_node_id") not in (None, ""):
            node.linked_node_id = id_map.get(str(item["linked_node_id"]))

    for item in nodes or []:
        old_id = item.get("id")
        old_parent = item.get("parent_id")
        if old_id in (None, "") or old_parent in (None, "", 0):
            continue
        real_id, real_parent = id_map.get(str(old_id)), id_map.get(str(old_parent))
        if real_id and real_parent:
            node = await db.get(RespNode, real_id)
            node.parent_id = real_parent

    for item in links or []:
        node_id = id_map.get(str(item.get("node_id")))
        user_id = str(item.get("user_id") or "").strip()
        if not node_id or not user_id:
            continue
        db.add(RespNodeLink(
            user_id=user_id,
            node_id=node_id,
            link_type=_norm(item.get("link_type"), LINK_TYPES, "solid"),
            role=_norm(item.get("role"), LINK_ROLES, "member"),
            source=_norm(item.get("source"), LINK_SOURCES, "manual"),
            confidence=item.get("confidence"),
        ))

    revision = await _bump(db, tree_id)
    await db.commit()
    return await get_tree_payload(db, tree_id)


# ── 挂载 ────────────────────────────────────────────────────

async def list_links(db: AsyncSession, node_id: Optional[int] = None,
                     user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    stmt = select(RespNodeLink)
    if node_id is not None:
        stmt = stmt.where(RespNodeLink.node_id == node_id)
    if user_id:
        stmt = stmt.where(RespNodeLink.user_id == user_id)
    rows = (await db.execute(stmt.order_by(RespNodeLink.id))).scalars().all()
    return [_link_out(x) for x in rows]


async def put_link(db: AsyncSession, payload: Dict[str, Any],
                   expected_revision: Optional[int] = None) -> Dict[str, Any]:
    """挂人 / 改角色。唯一键是 (user_id, node_id)，同人同节点只留一行。"""
    node_id = int(payload.get("node_id") or 0)
    user_id = str(payload.get("user_id") or "").strip()
    if not node_id or not user_id:
        raise ValueError("node_id 与 user_id 必填")
    node = await db.get(RespNode, node_id)
    if node is None or node.is_deleted:
        raise LookupError(f"节点不存在: id={node_id}")
    tree = await _get_tree(db, node.tree_id)
    await _check_revision(db, tree, expected_revision)

    link = (await db.execute(select(RespNodeLink).where(
        RespNodeLink.user_id == user_id, RespNodeLink.node_id == node_id))).scalars().first()
    if link is None:
        link = RespNodeLink(user_id=user_id, node_id=node_id)
        db.add(link)
    link.link_type = _norm(payload.get("link_type"), LINK_TYPES, link.link_type or "solid")
    link.role = _norm(payload.get("role"), LINK_ROLES, link.role or "member")
    link.source = _norm(payload.get("source"), LINK_SOURCES, link.source or "manual")
    if "confidence" in payload:
        link.confidence = payload.get("confidence")

    revision = await _bump(db, node.tree_id)
    await db.commit()
    await db.refresh(link)
    return {"link": _link_out(link), "revision": revision}


async def put_links(db: AsyncSession, items: List[Dict[str, Any]],
                    expected_revision: Optional[int] = None) -> Dict[str, Any]:
    """批量挂载（「一键把建议的人全挂上」用）。整批只 bump 一次 revision。"""
    if not items:
        return {"links": [], "revision": None}
    first_node = await db.get(RespNode, int(items[0].get("node_id") or 0))
    if first_node is None or first_node.is_deleted:
        raise LookupError(f"节点不存在: id={items[0].get('node_id')}")
    tree = await _get_tree(db, first_node.tree_id)
    await _check_revision(db, tree, expected_revision)

    out: List[Dict[str, Any]] = []
    for item in items:
        node_id = int(item.get("node_id") or 0)
        user_id = str(item.get("user_id") or "").strip()
        if not node_id or not user_id:
            continue
        link = (await db.execute(select(RespNodeLink).where(
            RespNodeLink.user_id == user_id, RespNodeLink.node_id == node_id))).scalars().first()
        if link is None:
            link = RespNodeLink(user_id=user_id, node_id=node_id)
            db.add(link)
        link.link_type = _norm(item.get("link_type"), LINK_TYPES, link.link_type or "solid")
        link.role = _norm(item.get("role"), LINK_ROLES, link.role or "member")
        link.source = _norm(item.get("source"), LINK_SOURCES, link.source or "manual")
        out.append({"user_id": user_id, "node_id": node_id,
                    "link_type": link.link_type, "role": link.role, "source": link.source})

    revision = await _bump(db, first_node.tree_id)
    await db.commit()
    return {"links": out, "revision": revision}


async def delete_link(db: AsyncSession, node_id: int, user_id: str,
                      expected_revision: Optional[int] = None) -> Dict[str, Any]:
    node = await db.get(RespNode, node_id)
    if node is None or node.is_deleted:
        raise LookupError(f"节点不存在: id={node_id}")
    tree = await _get_tree(db, node.tree_id)
    await _check_revision(db, tree, expected_revision)

    await db.execute(delete(RespNodeLink).where(
        RespNodeLink.node_id == node_id, RespNodeLink.user_id == user_id))
    revision = await _bump(db, node.tree_id)
    await db.commit()
    return {"node_id": node_id, "user_id": user_id, "revision": revision}
