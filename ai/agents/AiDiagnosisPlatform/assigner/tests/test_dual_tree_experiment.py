"""双树实验算法：提名、点亮、连通、汇合。不碰正式派单。"""

import asyncio

from ai.agents.AiDiagnosisPlatform.assigner.dual_tree.algorithm import (
    DualTreeExperiment,
    WalkEvent,
    check_trace,
    light_skill,
    merge_people,
    nominate,
)
from ai.agents.AiDiagnosisPlatform.assigner.dual_tree.catalog import Catalog, OrgNode, SkillLeaf


def _leaf(lid, name, iface, engineers, keywords=None, product="调度USP"):
    return SkillLeaf(
        id=lid,
        product=product,
        iface=iface,
        name=name,
        keywords=keywords or [],
        anchor="",
        engineer_ids=engineers,
        domain="调度系统",
    )


def _catalog():
    leaves = [
        _leaf("1", "路径规划", "调度", ["u-path"], keywords=["规划路径中"]),
        _leaf("2", "任务分配", "调度", ["u-task"], keywords=["调度中"]),
        _leaf("3", "地图编辑", "地图", ["u-map"], keywords=["导图"]),
    ]
    dept = OrgNode(
        id="d1",
        name="智能规划研究院",
        domain="调度系统",
        profile_text="调度",
        member_ids=["u-path", "u-org"],
    )
    return Catalog(
        leaves=leaves,
        departments=[dept],
        names={"u-path": "甲", "u-task": "乙", "u-map": "丙", "u-org": "丁"},
    )


class _Llm:
    def __init__(self, answers):
        self.answers = list(answers)

    async def complete(self, prompt, **kwargs):
        return self.answers.pop(0)


class TestNominateAndLight:
    def test_keyword_nominates_matching_leaf(self):
        """正常流程：正文出现界面状态时，只提名对上的叶子。"""
        found = nominate(_catalog().leaves, "叉车全部停在规划路径中")
        assert [leaf.name for leaf in found] == ["路径规划"]

    def test_same_iface_neighbor_connects(self):
        """正常流程：同一界面下的次高叶子成为相邻站，并连到这个界面。"""
        leaves = _catalog().leaves[:2]
        lit, bridges, neighbors = light_skill(leaves, {"1": 0.9, "2": 0.7})
        assert [leaf.name for leaf in lit] == ["路径规划", "任务分配"]
        assert [leaf.name for leaf in neighbors] == ["任务分配"]
        assert bridges == [("调度", "调度USP")]

    def test_different_iface_does_not_become_neighbor(self):
        """正常流程：不同界面的叶子不因为权重接近就连成相邻站。"""
        leaves = [_catalog().leaves[0], _catalog().leaves[2]]
        lit, bridges, neighbors = light_skill(leaves, {"1": 0.9, "3": 0.8})
        assert [leaf.name for leaf in lit] == ["路径规划"]
        assert neighbors == []
        assert bridges == []


class TestMerge:
    def test_both_sides_rank_first(self):
        """正常流程：两棵树都有的人排在只有一边的人前面。"""
        hits = merge_people(
            {"u-path": ["路径规划"], "u-task": ["任务分配"]},
            {"u-path": ["智能规划研究院"], "u-org": ["智能规划研究院"]},
            _catalog().names,
        )
        assert [hit.name for hit in hits] == ["甲", "乙", "丁"]
        assert hits[0].sides == ["技能", "组织"]
        assert hits[1].sides == ["技能"]
        assert hits[2].sides == ["组织"]


class TestWalk:
    def test_missing_skill_still_returns_org_people(self):
        """正常流程：技能树没有提名时，组织树选定的部门仍能给出人。"""
        llm = _Llm([
            '{"domains":[{"name":"调度系统","weight":0.9,"reason":"停在规划"}]}',
            '{"department_id":"d1","reason":"调度部门"}',
        ])
        result = asyncio.run(DualTreeExperiment(_catalog()).run(
            {"title": "页面打不开", "problem_description": "后台白屏", "robot_type": ""},
            llm,
        ))
        assert result.issues == []
        assert [person.name for person in result.people] == ["甲", "丁"]
        assert all(person.sides == ["组织"] for person in result.people)

    def test_vehicle_anchor_opens_department(self):
        """正常流程：车型对照打开部门，不再请模型选部门。"""
        llm = _Llm([
            '{"domains":[{"name":"调度系统","weight":0.8,"reason":"调度"}]}',
            '{"leaves":[{"id":"1","weight":0.9,"reason":"规划路径中"}]}',
        ])
        result = asyncio.run(DualTreeExperiment(
            _catalog(),
            vehicle_map={"叉车": "d1"},
        ).run(
            {"title": "全部叉车卡在规划路径中", "problem_description": "", "robot_type": "叉车机器人"},
            llm,
        ))
        assert result.issues == []
        assert any(event.action == "打开" and event.node_name == "智能规划研究院" for event in result.events)
        assert result.people[0].name == "甲"
        assert result.people[0].sides == ["技能", "组织"]

    def test_light_without_nominate_is_invalid(self):
        """异常流程：没有提名就点亮，痕迹检查会指出来。"""
        issues = check_trace([
            WalkEvent("点亮", "技能", "1", "路径规划", "调度", "点亮", "主站"),
        ])
        assert issues
