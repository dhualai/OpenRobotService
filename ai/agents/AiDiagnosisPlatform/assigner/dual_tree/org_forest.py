"""组织树森林。每个部门一棵树，部门之间不连线。人按所属部门挂到树上。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai.agents.AiDiagnosisPlatform.assigner.dual_tree.catalog import DEPT_DOMAIN, OrgNode


@dataclass
class ForestUser:
    id: str
    name: str
    department_id: str
    department_name: str


@dataclass
class ForestDepartment:
    id: str
    name: str
    company_name: str
    status: str
    profile_text: str


@dataclass
class OrgForest:
    trees: list[OrgNode] = field(default_factory=list)
    company_of: dict[str, str] = field(default_factory=dict)
    unmatched: list[tuple[str, str]] = field(default_factory=list)

    def render(self, names: dict[str, str]) -> str:
        lines = [
            "组织树是一片森林。每个部门单独一棵树，部门之间没有连线。",
            f"共 {len(self.trees)} 棵树，未挂载 {len(self.unmatched)} 人。",
            "",
        ]
        for node in self.trees:
            company = self.company_of.get(node.id) or "未填公司"
            lines.append(f"【{node.name}】公司：{company}　{len(node.member_ids)} 人")
            if not node.member_ids:
                lines.append("  （暂无人员）")
            for uid in node.member_ids:
                lines.append(f"  - {names.get(uid) or uid}")
            lines.append("")
        if self.unmatched:
            lines.append("【未挂载】")
            for uid, reason in self.unmatched:
                lines.append(f"  - {names.get(uid) or uid}：{reason}")
        return "\n".join(lines).rstrip() + "\n"


def build_org_forest(
    users: list[ForestUser],
    departments: list[ForestDepartment],
) -> OrgForest:
    """用人员上的部门把人挂到对应部门树上。同名部门多于一个时，不靠名称猜测。"""
    approved = [dept for dept in departments if (dept.status or "").strip() == "approved"]
    by_id = {dept.id: dept for dept in approved}
    name_hits: dict[str, list[ForestDepartment]] = {}
    for dept in approved:
        name_hits.setdefault(dept.name, []).append(dept)

    members: dict[str, list[str]] = {dept.id: [] for dept in approved}
    unmatched: list[tuple[str, str]] = []
    for user in users:
        dept_id = (user.department_id or "").strip()
        if dept_id and dept_id in by_id:
            members[dept_id].append(user.id)
            continue
        if dept_id:
            unmatched.append((user.id, "部门编号对不上现有部门"))
            continue
        label = (user.department_name or "").strip()
        if not label:
            unmatched.append((user.id, "没有部门"))
            continue
        hits = name_hits.get(label) or []
        if len(hits) == 1:
            members[hits[0].id].append(user.id)
            continue
        if len(hits) > 1:
            unmatched.append((user.id, f"部门名「{label}」对应多个部门"))
            continue
        unmatched.append((user.id, f"部门名「{label}」没有对应部门"))
    order = {user.id: user.name or user.id for user in users}
    for dept_id in members:
        members[dept_id].sort(key=lambda uid: order.get(uid, uid))
    unmatched.sort(key=lambda item: order.get(item[0], item[0]))

    trees = []
    company_of = {}
    for dept in sorted(approved, key=lambda item: (item.company_name or "", item.name)):
        trees.append(OrgNode(
            id=dept.id,
            name=dept.name,
            domain=DEPT_DOMAIN.get(dept.name, ""),
            profile_text=dept.profile_text or "",
            member_ids=members.get(dept.id) or [],
        ))
        company_of[dept.id] = dept.company_name or ""
    return OrgForest(trees=trees, company_of=company_of, unmatched=unmatched)


def dump_org_forest_log() -> str:
    """只读数据库，把组织森林写到日志。不改用户，不改部门。"""
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[5]
    backend = root / "backend"
    for item in (str(root), str(backend)):
        if item not in sys.path:
            sys.path.insert(0, item)
    from app.core.db import SessionLocal
    from ai.agents.AiDiagnosisPlatform.assigner.dual_tree.catalog import load_catalog

    db = SessionLocal()
    try:
        catalog = load_catalog(db)
    finally:
        db.close()
    text = catalog.org_forest.render(catalog.names)
    path = root / "ai" / "logs" / "assigner" / "org_forest.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


if __name__ == "__main__":
    print(dump_org_forest_log())

