"""组织森林：部门互不连接，人挂在自己的部门上。"""

from ai.agents.AiDiagnosisPlatform.assigner.dual_tree.org_forest import (
    ForestDepartment,
    ForestUser,
    build_org_forest,
)


def _dept(did, name, company="中力", status="approved"):
    return ForestDepartment(did, name, company, status, "")


class TestOrgForest:
    def test_each_department_is_its_own_tree(self):
        """正常流程：两个部门各成一棵树，人只出现在自己的部门下。"""
        forest = build_org_forest(
            [
                ForestUser("u1", "甲", "d1", ""),
                ForestUser("u2", "乙", "d2", ""),
            ],
            [_dept("d1", "智能规划研究院"), _dept("d2", "机器人事业部", company="中力")],
        )
        assert {node.name for node in forest.trees} == {"机器人事业部", "智能规划研究院"}
        by_name = {node.name: node.member_ids for node in forest.trees}
        assert by_name["智能规划研究院"] == ["u1"]
        assert by_name["机器人事业部"] == ["u2"]
        assert forest.unmatched == []

    def test_unique_department_name_mounts_user(self):
        """正常流程：只有部门名称、没有编号时，名称唯一就挂上。"""
        forest = build_org_forest(
            [ForestUser("u1", "甲", "", "质量部")],
            [_dept("d1", "质量部")],
        )
        assert forest.trees[0].member_ids == ["u1"]

    def test_missing_department_stays_unmounted(self):
        """异常流程：没有部门的人不挂到任何树上。"""
        forest = build_org_forest(
            [ForestUser("u1", "甲", "", "")],
            [_dept("d1", "质量部")],
        )
        assert forest.trees[0].member_ids == []
        assert forest.unmatched == [("u1", "没有部门")]

    def test_rejected_department_is_not_a_tree(self):
        """异常流程：未通过的部门不进入森林。"""
        forest = build_org_forest(
            [],
            [_dept("d1", "人事部", status="rejected")],
        )
        assert forest.trees == []
