"""实验用双树派单。不进入 DispatchFlow，不写 task_dispatch_log，不改工单。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ai.agents.AiDiagnosisPlatform.assigner.dual_tree.catalog import (
    DOMAINS,
    Catalog,
    OrgNode,
    SkillLeaf,
)
from ai.agents.AiDiagnosisPlatform.assigner.prompts.shared import USP_DISPATCH_BRIEF


DOMAIN_BRIEFS = {
    "调度系统": USP_DISPATCH_BRIEF,
    "车体硬件": "车体结构、机械和电气。轮子、电池、电机、货叉、车体碰撞这一类。不同部门负责不同车型。",
    "车端软件": "车上的控制器、定位、感知和车载通信。定位漂移、雷达无数据、控制器报错归这里。",
    "摇人吧": "摇人吧服务号里的小程序、后台、AI 和子界面，不是车上软件，也不是地面调度。",
    "上游对接": "与客户上位系统、仓储或产线系统的对接。",
    "实施工具": "现场实施、部署和调试用的工具与流程。",
    "售前": "方案、选型、报价，以及交付前的咨询。",
}


@dataclass
class WalkEvent:
    action: str
    tree: str
    node_id: str
    node_name: str
    parent_name: str
    state: str
    detail: str
    people: list[dict] = field(default_factory=list)


@dataclass
class PersonHit:
    user_id: str
    name: str
    sides: list[str]
    nodes: list[str]


@dataclass
class ExperimentResult:
    events: list[WalkEvent]
    people: list[PersonHit]
    domains: list[dict]
    issues: list[str]

    def render(self) -> str:
        lines = ["【领域】"]
        if not self.domains:
            lines.append("没有判定出领域。")
        for item in self.domains:
            mark = "主判断" if item.get("primary") else "次判断"
            lines.append(f"{mark} {item.get('name')} 权重 {item.get('weight')}。{item.get('reason') or ''}")
        lines.append("")
        lines.append("【游走】")
        for idx, event in enumerate(self.events, 1):
            who = ""
            if event.people:
                who = " 人：" + "、".join(p.get("name") or p.get("user_id") for p in event.people)
            lines.append(
                f"{idx}. [{event.tree}] {event.action} {event.node_name or '—'} "
                f"→ {event.state}。{event.detail}{who}"
            )
        lines.append("")
        lines.append("【汇合】")
        if not self.people:
            lines.append("两路都没有人。")
        for person in self.people:
            lines.append(
                f"{person.name}（{'、'.join(person.sides)}）节点：{'、'.join(person.nodes)}"
            )
        if self.issues:
            lines.append("")
            lines.append("【痕迹问题】")
            lines.extend(self.issues)
        return "\n".join(lines)


def _loads(raw: str) -> dict:
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return {}
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def nominate(leaves: list[SkillLeaf], text: str) -> list[SkillLeaf]:
    """正文对上关键词、功能名或锚文本的叶子。历史叶子记忆尚未接入。"""
    blob = text or ""
    found: list[SkillLeaf] = []
    seen: set[str] = set()
    for leaf in leaves:
        tokens = [leaf.name, leaf.anchor, *leaf.keywords]
        if any(token and token in blob for token in tokens):
            if leaf.id not in seen:
                found.append(leaf)
                seen.add(leaf.id)
    return found


def light_skill(
    nominated: list[SkillLeaf],
    weights: dict[str, float],
) -> tuple[list[SkillLeaf], list[tuple[str, str]], list[SkillLeaf]]:
    """点亮主站和同一界面下的相邻站。返回 (点亮, 连通节点, 相邻)。"""
    scored = [(leaf, float(weights.get(leaf.id) or 0.0)) for leaf in nominated]
    scored = [(leaf, weight) for leaf, weight in scored if weight > 0]
    if not scored:
        return [], [], []
    scored.sort(key=lambda item: item[1], reverse=True)
    primary, top = scored[0]
    lit = [primary]
    neighbors = []
    for leaf, weight in scored[1:]:
        same_parent = leaf.product == primary.product and leaf.iface == primary.iface
        if same_parent and weight >= top * 0.6:
            lit.append(leaf)
            neighbors.append(leaf)
    bridges: list[tuple[str, str]] = []
    if len(lit) >= 2:
        ifaces = {leaf.iface for leaf in lit}
        if len(ifaces) == 1:
            bridges.append((primary.iface or primary.product, primary.product))
        else:
            bridges.append((primary.product, primary.domain))
            for iface in sorted(ifaces):
                if iface:
                    bridges.append((iface, primary.product))
    return lit, bridges, neighbors


def merge_people(
    skill: dict[str, list[str]],
    org: dict[str, list[str]],
    names: dict[str, str],
) -> list[PersonHit]:
    """两边都有的人在前，其次仅技能，再次仅组织。"""
    both = [uid for uid in skill if uid in org]
    skill_only = [uid for uid in skill if uid not in org]
    org_only = [uid for uid in org if uid not in skill]
    ordered = both + skill_only + org_only
    hits = []
    for uid in ordered:
        sides = []
        nodes: list[str] = []
        if uid in skill:
            sides.append("技能")
            nodes.extend(skill[uid])
        if uid in org:
            sides.append("组织")
            nodes.extend(org[uid])
        hits.append(PersonHit(
            user_id=uid,
            name=names.get(uid) or uid,
            sides=sides,
            nodes=nodes,
        ))
    return hits


def check_trace(events: list[WalkEvent]) -> list[str]:
    """点亮必须有前因，取人只能来自已点亮的节点。"""
    skill_ready: set[str] = set()
    org_ready: set[str] = set()
    lit: set[str] = set()
    issues = []
    for event in events:
        key = f"{event.tree}:{event.node_id}"
        if event.action == "提名" and event.tree == "技能":
            skill_ready.add(event.node_id)
        if event.action == "加权" and event.tree == "技能":
            skill_ready.add(event.node_id)
        if event.action in {"打开", "看过"} and event.tree == "组织":
            org_ready.add(event.node_id)
        if event.action == "点亮":
            ready = skill_ready if event.tree == "技能" else org_ready
            if event.node_id not in ready:
                issues.append(f"{event.node_name} 被点亮，但前面没有提名或打开。")
            lit.add(key)
        if event.action == "连通" and event.state == "点亮":
            issues.append(f"{event.node_name} 被标成连通，却写成了点亮。")
        if event.action == "取人" and key not in lit:
            issues.append(f"从 {event.node_name} 取人，但该节点没有点亮。")
    return issues


def match_vehicle(robot_type: str, vehicle_map: dict[str, str]) -> str:
    text = (robot_type or "").strip()
    if not text:
        return ""
    for key, dept_id in vehicle_map.items():
        if key and (key in text or text in key):
            return dept_id
    return ""


class DualTreeExperiment:
    """按领域分别搜索两棵树，留下游走痕迹。"""

    def __init__(self, catalog: Catalog, vehicle_map: dict[str, str] | None = None):
        self.catalog = catalog
        self.vehicle_map = vehicle_map or {}

    async def run(self, ticket: dict, llm) -> ExperimentResult:
        events: list[WalkEvent] = []
        text = f"{ticket.get('title') or ''}\n{ticket.get('problem_description') or ''}"
        domains = await self._judge_domains(text, llm, events)
        skill_people: dict[str, list[str]] = {}
        org_people: dict[str, list[str]] = {}
        for item in domains:
            domain = item["name"]
            leaves = self.catalog.leaves_in(domain)
            nominated = nominate(leaves, text)
            weights = {}
            if nominated:
                weights = await weigh_leaves(llm, domain, text, nominated)
            org_id = ""
            departments = self.catalog.departments_in(domain)
            if departments and not match_vehicle(str(ticket.get("robot_type") or ""), self.vehicle_map):
                org_id = await choose_department(llm, domain, text, departments)
            self._search_skill(domain, text, weights, events, skill_people)
            self._search_org(domain, ticket, org_id, events, org_people)
        people = merge_people(skill_people, org_people, self.catalog.names)
        if people:
            both = [p for p in people if len(p.sides) == 2]
            skill_only = [p for p in people if p.sides == ["技能"]]
            org_only = [p for p in people if p.sides == ["组织"]]
            events.append(WalkEvent(
                action="汇合",
                tree="两路",
                node_id="",
                node_name="",
                parent_name="",
                state="汇合",
                detail=(
                    f"两边都有 {len(both)} 人，仅技能 {len(skill_only)} 人，仅组织 {len(org_only)} 人。"
                ),
                people=[{"user_id": p.user_id, "name": p.name, "sides": p.sides} for p in people],
            ))
        else:
            events.append(WalkEvent(
                action="汇合",
                tree="两路",
                node_id="",
                node_name="",
                parent_name="",
                state="这一侧为空",
                detail="两路都没有人，实验不走生产兜底。",
            ))
        return ExperimentResult(
            events=events,
            people=people,
            domains=domains,
            issues=check_trace(events),
        )

    async def _judge_domains(self, text: str, llm, events: list[WalkEvent]) -> list[dict]:
        lines = ["判断这张单落在哪一个问题领域。只能从下面的名单里选，不要自造领域。"]
        for name in DOMAINS:
            lines.append(f"- {name}：{DOMAIN_BRIEFS[name][:180]}")
        lines.append("输出 JSON：{\"domains\":[{\"name\":\"调度系统\",\"weight\":0.8,\"reason\":\"一句话\"}]}")
        lines.append("最多两个。weight 取 0 到 1。")
        lines.append(text[:2000])
        raw = await llm.complete("\n".join(lines), max_tokens=500, temperature=0.0)
        data = _loads(raw)
        picked = []
        for item in data.get("domains") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name not in DOMAINS:
                continue
            try:
                weight = float(item.get("weight") or 0)
            except (TypeError, ValueError):
                weight = 0
            picked.append({
                "name": name,
                "weight": weight,
                "reason": str(item.get("reason") or "").strip(),
            })
        picked.sort(key=lambda item: item["weight"], reverse=True)
        picked = picked[:2]
        if len(picked) == 2 and picked[1]["weight"] < 0.35:
            picked = picked[:1]
        for idx, item in enumerate(picked):
            item["primary"] = idx == 0
            events.append(WalkEvent(
                action="判定领域",
                tree="入口",
                node_id=item["name"],
                node_name=item["name"],
                parent_name="",
                state="点亮",
                detail=("主判断。" if item["primary"] else "次判断。") + item["reason"],
            ))
        if not picked:
            events.append(WalkEvent(
                action="判定领域",
                tree="入口",
                node_id="",
                node_name="",
                parent_name="",
                state="这一侧为空",
                detail="模型没有给出名单内的领域。",
            ))
        return picked

    def _search_skill(self, domain, text, preset_weights, events, people):
        leaves = self.catalog.leaves_in(domain)
        nominated = nominate(leaves, text)
        if not nominated:
            events.append(WalkEvent(
                action="这一侧为空",
                tree="技能",
                node_id=domain,
                node_name=domain,
                parent_name="",
                state="这一侧为空",
                detail="没有关键词提名。尚无叶子记忆。不把全部叶子交给模型。",
            ))
            return
        note = "尚无叶子记忆，只使用正文提名。"
        for idx, leaf in enumerate(nominated):
            events.append(WalkEvent(
                action="提名",
                tree="技能",
                node_id=leaf.id,
                node_name=leaf.name,
                parent_name=leaf.parent_name,
                state="提名",
                detail=(note if idx == 0 else "") + f"产品 {leaf.product}，界面 {leaf.iface or '无'}。",
            ))
        weights = {leaf.id: float(preset_weights.get(leaf.id) or 0) for leaf in nominated}
        if not any(weights.values()):
            events.append(WalkEvent(
                action="这一侧为空",
                tree="技能",
                node_id=domain,
                node_name=domain,
                parent_name="",
                state="这一侧为空",
                detail="模型没有给提名叶子写出有效权重。",
            ))
            return
        for leaf in nominated:
            events.append(WalkEvent(
                action="加权",
                tree="技能",
                node_id=leaf.id,
                node_name=leaf.name,
                parent_name=leaf.parent_name,
                state="提名",
                detail=f"权重 {weights[leaf.id]:.2f}。",
            ))
        lit, bridges, neighbors = light_skill(nominated, weights)
        neighbor_ids = {leaf.id for leaf in neighbors}
        for leaf in lit:
            role = "相邻站" if leaf.id in neighbor_ids else "主站"
            events.append(WalkEvent(
                action="点亮",
                tree="技能",
                node_id=leaf.id,
                node_name=leaf.name,
                parent_name=leaf.parent_name,
                state="点亮",
                detail=role,
            ))
            taken = [
                {"user_id": uid, "name": self.catalog.name_of(uid)}
                for uid in leaf.engineer_ids
            ]
            events.append(WalkEvent(
                action="取人",
                tree="技能",
                node_id=leaf.id,
                node_name=leaf.name,
                parent_name=leaf.parent_name,
                state="点亮",
                detail="实线挂载。" if taken else "这个叶子没有实线挂载。",
                people=taken,
            ))
            for uid in leaf.engineer_ids:
                people.setdefault(uid, [])
                if leaf.name not in people[uid]:
                    people[uid].append(leaf.name)
        for name, parent in bridges:
            events.append(WalkEvent(
                action="连通",
                tree="技能",
                node_id=name,
                node_name=name,
                parent_name=parent,
                state="连通",
                detail="连接已点亮的叶子，桥上不取人。",
            ))
        if not lit:
            events.append(WalkEvent(
                action="这一侧为空",
                tree="技能",
                node_id=domain,
                node_name=domain,
                parent_name="",
                state="这一侧为空",
                detail="提名都没有正的权重。",
            ))

    def _search_org(self, domain, ticket, preset_id, events, people):
        nodes = self.catalog.departments_in(domain)
        if not nodes:
            events.append(WalkEvent(
                action="这一侧为空",
                tree="组织",
                node_id=domain,
                node_name=domain,
                parent_name="",
                state="这一侧为空",
                detail="这个领域还没有挂上部门。",
            ))
            return
        chosen = ""
        robot = str(ticket.get("robot_type") or "")
        anchored = match_vehicle(robot, self.vehicle_map)
        by_id = {node.id: node for node in nodes}
        if anchored and anchored in by_id:
            chosen = anchored
            node = by_id[anchored]
            events.append(WalkEvent(
                action="打开",
                tree="组织",
                node_id=node.id,
                node_name=node.name,
                parent_name=domain,
                state="打开",
                detail=f"车型锚点：{robot}。",
            ))
        elif preset_id and preset_id in by_id:
            chosen = preset_id
            node = by_id[preset_id]
            for other in nodes:
                if other.id == node.id:
                    continue
                events.append(WalkEvent(
                    action="看过",
                    tree="组织",
                    node_id=other.id,
                    node_name=other.name,
                    parent_name=domain,
                    state="看过",
                    detail="没有被选中。",
                ))
            events.append(WalkEvent(
                action="看过",
                tree="组织",
                node_id=node.id,
                node_name=node.name,
                parent_name=domain,
                state="看过",
                detail="没有固定锚点，在本领域已有部门里选定。",
            ))
        else:
            events.append(WalkEvent(
                action="这一侧为空",
                tree="组织",
                node_id=domain,
                node_name=domain,
                parent_name="",
                state="这一侧为空",
                detail="没有车型或项目锚点，也没有选定部门。尚无组织结案记忆。",
            ))
            return
        node = by_id[chosen]
        events.append(WalkEvent(
            action="点亮",
            tree="组织",
            node_id=node.id,
            node_name=node.name,
            parent_name=domain,
            state="点亮",
            detail="没有下级组，人从该部门的实线成员里取。",
        ))
        taken = [
            {"user_id": uid, "name": self.catalog.name_of(uid)}
            for uid in node.member_ids
        ]
        events.append(WalkEvent(
            action="取人",
            tree="组织",
            node_id=node.id,
            node_name=node.name,
            parent_name=domain,
            state="点亮",
            detail="实线成员。" if taken else "这个部门没有实线成员。",
            people=taken,
        ))
        for uid in node.member_ids:
            people.setdefault(uid, [])
            if node.name not in people[uid]:
                people[uid].append(node.name)


async def weigh_leaves(llm, domain: str, text: str, nominated: list[SkillLeaf]) -> dict[str, float]:
    lines = [
        f"领域是{domain}。只给下面这些已经提名的叶子打权重，不要新增叶子。",
        DOMAIN_BRIEFS.get(domain, ""),
        "weight 取 0 到 1。输出 JSON：{\"leaves\":[{\"id\":\"叶子id\",\"weight\":0.8,\"reason\":\"一句话\"}]}",
    ]
    for leaf in nominated:
        lines.append(f"- id={leaf.id} 名称={leaf.name} 界面={leaf.iface} 说明={leaf.anchor[:80]}")
    lines.append(text[:2000])
    raw = await llm.complete("\n".join(lines), max_tokens=800, temperature=0.0)
    data = _loads(raw)
    allowed = {leaf.id for leaf in nominated}
    weights = {}
    for item in data.get("leaves") or []:
        if not isinstance(item, dict):
            continue
        lid = str(item.get("id") or "").strip()
        if lid not in allowed:
            continue
        try:
            weights[lid] = float(item.get("weight") or 0)
        except (TypeError, ValueError):
            continue
    return weights


async def choose_department(llm, domain: str, text: str, nodes: list[OrgNode]) -> str:
    if not nodes:
        return ""
    lines = [
        f"领域是{domain}。没有车型锚点。只能从下面这些部门里选一个，不要自造部门。",
        "输出 JSON：{\"department_id\":\"部门id\",\"reason\":\"一句话\"}",
    ]
    for node in nodes:
        brief = (node.profile_text or "")[:160]
        lines.append(f"- id={node.id} 名称={node.name} {brief}")
    lines.append(text[:2000])
    raw = await llm.complete("\n".join(lines), max_tokens=300, temperature=0.0)
    data = _loads(raw)
    dept_id = str(data.get("department_id") or "").strip()
    allowed = {node.id for node in nodes}
    return dept_id if dept_id in allowed else ""
