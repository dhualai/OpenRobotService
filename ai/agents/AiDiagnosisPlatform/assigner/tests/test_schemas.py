"""schemas 数据模型：字段验证 / collected_info 补栏 / EngineerProfile 方法 / dispatch_hint_text。

不调 LLM、不连库。
运行（仓库根）：
    pytest ai/agents/AiDiagnosisPlatform/assigner/tests/test_schemas.py -v
"""

import pytest

from ai.agents.AiDiagnosisPlatform.assigner.schemas import (
    AssignmentResult,
    EngineerProfile,
    TicketContext,
    collected_value,
    dispatch_hint_text,
)


# ── TicketContext ──

class TestTicketContextValidation:
    """TicketContext 必填字段校验 + collected_info 补栏。"""

    def test_minimal_fields_ok(self):
        """正常流程：只有 id / title / description / status 也能构造。"""
        t = TicketContext(id=1, title="test", problem_description="desc", status="new")
        assert t.priority is None
        assert t.fault_code is None

    def test_id_accepts_str_and_int(self):
        """正常流程：id 兼容字符串和整数。"""
        t1 = TicketContext(id="TK-001", title="t", problem_description="d", status="new")
        t2 = TicketContext(id=42, title="t", problem_description="d", status="new")
        assert t1.id == "TK-001"
        assert t2.id == 42

    def test_extra_fields_ignored(self):
        """正常流程：model_config extra=ignore，未知字段不报错。"""
        t = TicketContext(
            id=1, title="t", problem_description="d", status="new",
            unknown_field="hello",  # type: ignore
        )
        assert not hasattr(t, "unknown_field")

    def test_empty_title_and_description_accepted(self):
        """边界条件：标题和描述都空 → Pydantic 允许（str 无 minLength），
        但 DispatchFlow.aassign 会在此场景抛 ValueError（见 test_e2e_flow）。
        """
        t = TicketContext(id=1, title="", problem_description="", status="new")
        assert t.title == ""
        assert t.problem_description == ""


class TestCollectedInfoFill:
    """collected_info 自动补栏：顶栏空时从诊断收集信息补充。"""

    def test_robot_type_filled_from_collected(self):
        """正常流程：robot_type 空时从 collected_info 补。"""
        t = TicketContext(
            id=1, title="故障", problem_description="车停了", status="new",
            diagnosis_collected_info={"robot_type": "S20", "fault_code": "E1001"},
        )
        assert t.robot_type == "S20"
        assert t.fault_code == "E1001"

    def test_existing_not_overwritten(self):
        """正常流程：顶栏已有值时不被 collected_info 覆盖。"""
        t = TicketContext(
            id=1, title="故障", problem_description="车停了", status="new",
            robot_type="原车型",
            diagnosis_collected_info={"robot_type": "新车型"},
        )
        assert t.robot_type == "原车型"

    def test_useless_collected_ignored(self):
        """正常流程：collected_info 中"无"/"未知"等占位词不补。"""
        for placeholder in ["", "无", "未知", "none", "null", "n/a", "无（不知道）"]:
            t = TicketContext(
                id=1, title="t", problem_description="d", status="new",
                diagnosis_collected_info={"robot_type": placeholder},
            )
            assert t.robot_type is None, f"占位词 '{placeholder}' 应不补"

    def test_non_dict_collected_raises(self):
        """异常流程：collected_info 不是 dict → Pydantic 校验失败。"""
        with pytest.raises(Exception):
            TicketContext(
                id=1, title="t", problem_description="d", status="new",
                diagnosis_collected_info="not a dict",  # type: ignore
            )


class TestCollectedValue:
    """collected_value 工具函数：空值/占位词过滤。"""

    @pytest.mark.parametrize("val,expected", [
        (None, None),
        ("", None),
        ("  ", None),
        ("无", None),
        ("未知", None),
        ("none", None),
        ("NULL", None),
        ("n/a", None),
        ("无（不知道）", None),
        ("无(n/a)", None),
        ("S20", "S20"),
        ("  E1001  ", "E1001"),
    ])
    def test_collected_value_filtering(self, val, expected):
        assert collected_value(val) == expected


class TestDispatchHintText:
    """dispatch_hint 枚举 → 注入话术。"""

    def test_lacking_hint(self):
        text = dispatch_hint_text("lacking")
        assert "信息不足" in text
        assert "建议正常派单" in text

    def test_severe_hint(self):
        text = dispatch_hint_text("severe")
        assert "严重不足" in text
        assert "对接人" in text

    def test_unknown_hint_empty(self):
        assert dispatch_hint_text("unknown") == ""
        assert dispatch_hint_text("") == ""
        assert dispatch_hint_text(None) == ""


# ── EngineerProfile ──

class TestEngineerProfileModules:
    """EngineerProfile 责任模块相关方法。"""

    def test_function_names_for_product_three_layer(self):
        """正常流程：三层结构 {产品: {界面: [功能]}} → 扁平功能列表。"""
        eng = EngineerProfile(
            id="u1", name="张三",
            responsibility_modules={
                "调度USP": {
                    "监控": ["路径规划", "交通管制"],
                    "任务": ["任务下发"],
                },
            },
        )
        assert sorted(eng.function_names_for_product("调度USP")) == ["交通管制", "任务下发", "路径规划"]

    def test_function_names_for_product_two_layer_compat(self):
        """兼容性：旧两层 {产品: [模块]} → 直接返回列表。"""
        eng = EngineerProfile(
            id="u1", name="张三",
            responsibility_modules={"调度USP": ["路径规划", "交通管制"]},
        )
        assert sorted(eng.function_names_for_product("调度USP")) == ["交通管制", "路径规划"]

    def test_function_names_for_product_flat_string_compat(self):
        """兼容性：旧扁平字符串值 → function_names_for_product 抛异常（不支持纯字符串值），
        需用 list 或 dict 包裹。"""
        eng = EngineerProfile(
            id="u1", name="张三",
            responsibility_modules={"调度USP": ["路径规划"]},  # list 形式
        )
        assert eng.function_names_for_product("调度USP") == ["路径规划"]

    def test_function_names_for_unknown_product_empty(self):
        """正常流程：产品不存在 → 空列表。"""
        eng = EngineerProfile(
            id="u1", name="张三",
            responsibility_modules={"调度USP": {"监控": ["路径规划"]}},
        )
        assert eng.function_names_for_product("车端硬件") == []

    def test_function_names_dedup_across_interfaces(self):
        """正常流程：跨界面同功能名去重。"""
        eng = EngineerProfile(
            id="u1", name="张三",
            responsibility_modules={
                "调度USP": {
                    "监控": ["路径规划", "交通管制"],
                    "地图": ["路径规划", "地图编辑"],
                },
            },
        )
        names = eng.function_names_for_product("调度USP")
        assert names.count("路径规划") == 1
        assert "地图编辑" in names

    def test_all_modules_cross_product_dedup(self):
        """正常流程：跨产品合并去重。"""
        eng = EngineerProfile(
            id="u1", name="张三",
            responsibility_modules={
                "调度USP": {"监控": ["路径规划"]},
                "车端软件": {"控制": ["路径规划", "车端控制"]},
            },
        )
        all_mods = eng.all_modules()
        assert all_mods.count("路径规划") == 1
        assert "车端控制" in all_mods

    def test_all_modules_empty(self):
        """正常流程：无责任模块 → 空列表。"""
        eng = EngineerProfile(id="u1", name="张三")
        assert eng.all_modules() == []

    def test_modules_display_three_layer(self):
        """正常流程：三层格式化展示。"""
        eng = EngineerProfile(
            id="u1", name="张三",
            responsibility_modules={
                "调度USP": {"监控": ["路径规划", "交通管制"]},
            },
        )
        display = eng.modules_display()
        assert "[调度USP]" in display
        assert "监控:[路径规划,交通管制]" in display

    def test_modules_display_two_layer_compat(self):
        """兼容性：旧两层格式化。"""
        eng = EngineerProfile(
            id="u1", name="张三",
            responsibility_modules={"调度USP": ["路径规划", "交通管制"]},
        )
        display = eng.modules_display()
        assert "[调度USP]路径规划,交通管制" == display

    def test_modules_display_empty_product(self):
        """正常流程：产品下无功能 → 只显示产品名。"""
        eng = EngineerProfile(
            id="u1", name="张三",
            responsibility_modules={"调度USP": {}},
        )
        assert eng.modules_display() == "[调度USP]"

    def test_modules_display_empty(self):
        """正常流程：无责任模块 → 空字符串。"""
        eng = EngineerProfile(id="u1", name="张三")
        assert eng.modules_display() == ""


# ── AssignmentResult ──

class TestAssignmentResult:
    """AssignmentResult 基本字段。"""

    def test_minimal_result(self):
        r = AssignmentResult(
            engineer_id="u1", engineer_name="张三",
            confidence_score=0.8, reasoning="ok", decision_type="auto",
        )
        assert r.preferred_id is None
        assert r.matched_pref is None
        assert r.name_collision is False
        assert r.pinyin_match is False
        assert r.profile is None
        assert r.candidates is None

    def test_with_optional_fields(self):
        r = AssignmentResult(
            engineer_id="u1", engineer_name="张三",
            confidence_score=0.9, reasoning="ok", decision_type="recommend",
            preferred_id="u1", matched_pref=True,
            name_collision=False, pinyin_match=True,
            profile={"dept": "dept", "missing": []},
            candidates=[{"rank": 1, "engineer_id": "u1"}],
        )
        assert r.matched_pref is True
        assert r.pinyin_match is True
        assert r.profile["missing"] == []
        assert len(r.candidates) == 1
