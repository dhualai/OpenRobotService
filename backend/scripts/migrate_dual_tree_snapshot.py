"""把双树试做的 JSON 快照灌进责任双树三张表（一次性迁移，可重复跑）。

快照格式同 `frontend/src/pages/admin/dualTreeTrialSnapshot.ts` 的 `TrialSnapshot`：
`{nodes: [{id, parentId, kind, orgKind, name, description, vehicleModels}], mounts: [{nodeId, userId, role}]}`，
`kind` 取值 `domain | org | skill`。

落库映射（见 `backend/app/models/resp_tree.py` 顶部说明）：

- 试做页是一棵混合树，这里拆成两棵：
  - **skill 树**：domain → `node_kind='umbrella'`；skill → `'module'`/`'leaf'`
  - **org 树**：domain → `node_kind='umbrella'`（`linked_node_id` 指回 skill 树同域节点）；
    orgKind=company/department → `node_kind='org'` + `org_kind`
- 同一个公司在多个域下各存一条（不做跨域去重）——现状即如此，是保真要求
- mounts → `resp_node_links`，role 直映，link_type='solid'，source='migrated_from_engineers'

用法（在 backend/ 目录下，用项目 venv）：
    ../.venv/Scripts/python.exe scripts/migrate_dual_tree_snapshot.py --dry-run
    ../.venv/Scripts/python.exe scripts/migrate_dual_tree_snapshot.py --force
`--force` 会先清空两棵树的节点/挂载再灌；不加则库中已有节点时直接退出，避免覆盖人工编辑。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select  # noqa: E402

from app.core.db import AsyncSessionLocal  # noqa: E402
from app.models.resp_tree import RespNode, RespNodeLink, RespTree  # noqa: E402
from app.modules.admin.services import resp_tree_service as svc  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SNAPSHOT = REPO_ROOT / ".local" / "责任双树" / "dual-tree-trial.snapshot.json"


def _order(by_id: Dict[str, dict], items: List[dict]) -> List[dict]:
    """按层级排序（父在前），防「先建子再建父」时拿不到父 id。"""
    depth: Dict[str, int] = {}

    def depth_of(node: dict, guard: set) -> int:
        nid = str(node.get("id"))
        if nid in depth:
            return depth[nid]
        if nid in guard:
            return 0
        parent = by_id.get(str(node.get("parentId") or ""))
        value = 0 if parent is None else depth_of(parent, guard | {nid}) + 1
        depth[nid] = value
        return value

    return sorted(items, key=lambda n: depth_of(n, set()))


def _read_snapshot(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise SystemExit(f"快照格式不对：{path}")
    return {"nodes": data.get("nodes") or [], "mounts": data.get("mounts") or []}


async def migrate(snapshot: Dict[str, Any], *, force: bool, dry_run: bool) -> int:
    nodes: List[dict] = snapshot["nodes"]
    mounts: List[dict] = snapshot["mounts"]
    by_id = {str(n["id"]): n for n in nodes}

    domains = [n for n in nodes if n.get("kind") == "domain"]
    skills = _order(by_id, [n for n in nodes if n.get("kind") == "skill"])
    orgs = _order(by_id, [n for n in nodes if n.get("kind") == "org"])

    async with AsyncSessionLocal() as db:
        trees = await svc.ensure_trees(db)
        tree_id = {t.kind: t.id for t in trees}
        skill_tree, org_tree = tree_id["skill"], tree_id["org"]

        existing = (await db.execute(
            select(RespNode).where(RespNode.tree_id.in_([skill_tree, org_tree]))
        )).scalars().all()
        if existing and not force:
            print(f"[STOP] 两棵树里已有 {len(existing)} 个节点。要覆盖请加 --force"
                  "（会清空节点与挂载后重灌）")
            return 1

        if dry_run:
            print(f"[DRY] 域 {len(domains)} / 组织 {len(orgs)} / 技能 {len(skills)} / 挂载 {len(mounts)}")
            return 0

        if existing:
            ids = [n.id for n in existing]
            await db.execute(delete(RespNodeLink).where(RespNodeLink.node_id.in_(ids)))
            await db.execute(delete(RespNode).where(RespNode.id.in_(ids)))
            await db.flush()
            print(f"[CLR] 清空原有 {len(ids)} 个节点及其挂载")

        skill_map: Dict[str, int] = {}
        org_map: Dict[str, int] = {}

        def add(**kwargs) -> RespNode:
            node = RespNode(**kwargs)
            db.add(node)
            return node

        # 1) skill 树：域 umbrella + 技能（技能可嵌套，父是域或别的技能）
        for item in domains:
            node = add(tree_id=skill_tree, parent_id=None, name=item["name"],
                       description=item.get("description") or "",
                       node_kind="umbrella", sort_order=1,
                       legacy_ref={"old_id": str(item["id"])})
            await db.flush()
            skill_map[str(item["id"])] = node.id

        for item in skills:
            parent_old = str(item.get("parentId") or "")
            node = add(tree_id=skill_tree, parent_id=skill_map.get(parent_old),
                       name=item["name"], description=item.get("description") or "",
                       node_kind="leaf", legacy_ref={"old_id": str(item["id"])})
            await db.flush()
            skill_map[str(item["id"])] = node.id

        # 2) org 树：域 umbrella（linked_node_id 指回 skill 树同域节点）+ 公司/部门
        for item in domains:
            node = add(tree_id=org_tree, parent_id=None, name=item["name"],
                       description=item.get("description") or "",
                       node_kind="umbrella", sort_order=1,
                       linked_node_id=skill_map.get(str(item["id"])),
                       legacy_ref={"old_id": str(item["id"])})
            await db.flush()
            org_map[str(item["id"])] = node.id

        for item in orgs:
            parent_old = str(item.get("parentId") or "")
            parent = org_map.get(parent_old)
            if parent is None:
                print(f"[WARN] 组织「{item['name']}」找不到父节点 {parent_old}，挂到所属域下兜底")
                domain_old = _domain_of(by_id, item)
                parent = org_map.get(domain_old)
            node = add(tree_id=org_tree, parent_id=parent, name=item["name"],
                       description=item.get("description") or "",
                       vehicle_models=item.get("vehicleModels") or "",
                       node_kind="org", org_kind=item.get("orgKind") or None,
                       legacy_ref={"old_id": str(item["id"])})
            await db.flush()
            org_map[str(item["id"])] = node.id

        # 3) 挂载：节点可能落在任一棵树上。
        # 试做页允许同一人在同一节点挂多个 role；库表唯一键是 (user_id, node_id)，
        # 冲突时保留优先级更高的角色（leader > owner > specialist > member）。
        role_rank = {"leader": 4, "owner": 3, "specialist": 2, "member": 1}
        picked: Dict[tuple, str] = {}
        added, skipped = 0, 0
        for item in mounts:
            node_id = org_map.get(str(item.get("nodeId"))) or skill_map.get(str(item.get("nodeId")))
            user_id = str(item.get("userId") or "").strip()
            if not node_id or not user_id:
                skipped += 1
                continue
            role = str(item.get("role") or "member")
            if role not in role_rank:
                role = "member"
            key = (user_id, node_id)
            prev = picked.get(key)
            if prev is None or role_rank[role] > role_rank[prev]:
                picked[key] = role
            else:
                skipped += 1
        for (user_id, node_id), role in picked.items():
            db.add(RespNodeLink(user_id=user_id, node_id=node_id, link_type="solid",
                                role=role, source="migrated_from_engineers"))
            added += 1
        if skipped:
            print(f"[DEDUP] 挂载去重/跳过 {skipped} 条")

        skill_tree_row = await db.get(RespTree, skill_tree)
        org_tree_row = await db.get(RespTree, org_tree)
        skill_tree_row.revision = int(skill_tree_row.revision or 0) + 1
        org_tree_row.revision = int(org_tree_row.revision or 0) + 1
        await db.commit()

        print(f"[OK] skill 树 {len(domains)} 域 / {len(skills)} 技能；"
              f"org 树 {len(domains)} 域 / {len(orgs)} 组织；挂载 {added}（跳过 {skipped}）")

        # 4) 对账：按域列出组织数，好一眼看出「车体硬件」这类域有没有缺
        print("\n按域组织数（对账用）：")
        for item in domains:
            count = _count_orgs_by_domain(orgs, by_id, str(item["id"]))
            print(f"  {item['name']}: {count}")
        return 0


def _domain_of(by_id: Dict[str, dict], node: dict) -> str:
    cur, guard = node, set()
    while cur is not None and str(cur.get("id")) not in guard:
        if cur.get("kind") == "domain":
            return str(cur["id"])
        guard.add(str(cur.get("id")))
        cur = by_id.get(str(cur.get("parentId") or ""))
    return ""


def _count_orgs_by_domain(orgs: List[dict], by_id: Dict[str, dict], domain_id: str) -> int:
    return sum(1 for o in orgs if _domain_of(by_id, o) == domain_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="把双树试做快照灌进 resp_* 三张表")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT,
                        help=f"快照 JSON 路径（默认 {DEFAULT_SNAPSHOT}）")
    parser.add_argument("--force", action="store_true", help="先清空两棵树的节点与挂载再灌")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()

    if not args.snapshot.exists():
        print(f"[ERR] 找不到快照：{args.snapshot}")
        return 1
    snapshot = _read_snapshot(args.snapshot)
    return asyncio.run(migrate(snapshot, force=args.force, dry_run=args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
