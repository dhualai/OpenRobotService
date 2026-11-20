"""实验用双树目录。只读 module_tree_nodes 与部门、人员，不写库。

本地还没有 resp_trees。技能叶子暂用责任模块功能行，组织节点暂用部门。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ai.agents.AiDiagnosisPlatform.assigner.dual_tree.domains import (
    DEPT_DOMAIN,
    DOMAINS,
    PRODUCT_DOMAIN,
)


@dataclass
class SkillLeaf:
    id: str
    product: str
    iface: str
    name: str
    keywords: list[str]
    anchor: str
    engineer_ids: list[str]
    domain: str

    @property
    def parent_name(self) -> str:
        return self.iface or self.product


@dataclass
class OrgNode:
    id: str
    name: str
    domain: str
    profile_text: str
    member_ids: list[str]


@dataclass
class Catalog:
    leaves: list[SkillLeaf] = field(default_factory=list)
    departments: list[OrgNode] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    org_forest: object | None = None

    def leaves_in(self, domain: str) -> list[SkillLeaf]:
        return [leaf for leaf in self.leaves if leaf.domain == domain]

    def departments_in(self, domain: str) -> list[OrgNode]:
        return [node for node in self.departments if node.domain == domain]

    def name_of(self, user_id: str) -> str:
        return self.names.get(user_id) or user_id


def load_catalog(db) -> Catalog:
    """从现有表装出实验目录。调用方负责打开和关闭会话。"""
    from app.models.identity import UserDB
    from app.models.module_tree_node import ModuleTreeNode
    from app.models.organization import Department

    users = db.query(UserDB).filter(UserDB.status == "active").all()
    names = {u.id: (u.name or u.username or u.id) for u in users}
    from ai.agents.AiDiagnosisPlatform.assigner.dual_tree.org_forest import (
        ForestDepartment,
        ForestUser,
        build_org_forest,
    )
    from app.models.organization import Company

    companies = {row.id: row.name for row in db.query(Company).all()}
    forest = build_org_forest(
        [
            ForestUser(
                id=user.id,
                name=names[user.id],
                department_id=user.department_id or "",
                department_name=user.department or "",
            )
            for user in users
        ],
        [
            ForestDepartment(
                id=row.id,
                name=(row.name or "").strip(),
                company_name=companies.get(row.company_id) or "",
                status=row.status or "",
                profile_text=(row.profile_text or "").strip(),
            )
            for row in db.query(Department).all()
        ],
    )

    leaves: list[SkillLeaf] = []
    for row in db.query(ModuleTreeNode).all():
        domain = PRODUCT_DOMAIN.get((row.product or "").strip(), "")
        if not domain:
            continue
        keywords = [str(k).strip() for k in (row.keywords or []) if str(k).strip()]
        engineers = [str(e).strip() for e in (row.engineers or []) if str(e).strip() in names]
        leaves.append(SkillLeaf(
            id=str(row.id),
            product=row.product,
            iface=(row.iface_name or "").strip(),
            name=(row.func_name or "").strip(),
            keywords=keywords,
            anchor=(row.anchor or "").strip(),
            engineer_ids=engineers,
            domain=domain,
        ))

    departments = list(forest.trees)
    return Catalog(leaves=leaves, departments=departments, names=names, org_forest=forest)
