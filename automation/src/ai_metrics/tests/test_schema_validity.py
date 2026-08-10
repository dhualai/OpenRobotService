"""Self-tests for ai_metrics (Fast Lane)."""

from automation.src.ai_metrics import (
    check_schema,
    hit_ratio,
    keyword_hit_passed,
    missing_keywords,
    resolve_path,
)


class TestResolvePath:
    def test_nested_dict(self):
        data = {"agent_state": {"phase": "diagnosing", "hypotheses": ["a"]}}
        assert resolve_path(data, "agent_state.phase") == "diagnosing"
        assert resolve_path(data, "agent_state.hypotheses") == ["a"]

    def test_missing_segment_returns_none(self):
        assert resolve_path({"a": {"b": 1}}, "a.c") is None
        assert resolve_path({}, "a.b") is None

    def test_list_index(self):
        data = {"items": [{"name": "x"}, {"name": "y"}]}
        assert resolve_path(data, "items.1.name") == "y"

    def test_primitive_shortcircuit(self):
        assert resolve_path({"a": "str"}, "a.b") is None


class TestCheckSchema:
    def test_valid_scalar_types(self):
        data = {"message": "hi", "round": 3, "score": 0.85, "ok": True, "tags": ["a"], "obj": {"k": 1}}
        schema = {
            "message": "str",
            "round": "int",
            "score": "float",
            "ok": "bool",
            "tags": "list",
            "obj": "dict",
        }
        assert check_schema(data, schema) == []

    def test_enum_allowed(self):
        data = {"action": "ask", "phase": "diagnosing"}
        assert check_schema(data, {"action": ["ask", "answer", "submit"]}) == []
        assert check_schema(data, {"phase": ["idle", "diagnosing", "resolved"]}) == []

    def test_enum_violation(self):
        data = {"action": "submit"}
        violations = check_schema(data, {"action": ["ask", "answer"]})
        assert len(violations) == 1
        assert "action" in violations[0]

    def test_type_violation(self):
        data = {"message": 123}
        violations = check_schema(data, {"message": "str"})
        assert len(violations) == 1

    def test_nested_dot_path(self):
        data = {"agent_state": {"phase": "resolved", "hypotheses": []}}
        assert check_schema(data, {"agent_state.phase": "str", "agent_state.hypotheses": "list"}) == []

    def test_missing_field_reported(self):
        data = {"message": "hi"}
        violations = check_schema(data, {"agent_state.phase": "str"})
        assert len(violations) == 1

    def test_any_type(self):
        assert check_schema({"k": None}, {"k": "any"}) == []

    def test_unknown_type_spec(self):
        violations = check_schema({"k": 1}, {"k": "datetime"})
        assert len(violations) == 1


class TestKeywordHit:
    def test_hit_ratio_full(self):
        assert hit_ratio("请确认路径规划和定位状态", ["路径规划", "定位"]) == 1.0

    def test_hit_ratio_partial(self):
        assert hit_ratio("请确认定位状态", ["路径规划", "定位"]) == 0.5

    def test_hit_ratio_case_insensitive(self):
        assert hit_ratio("MQTT Connected", ["mqtt"]) == 1.0

    def test_empty_text(self):
        assert hit_ratio("", ["a"]) == 0.0

    def test_empty_keywords(self):
        assert hit_ratio("anything", []) == 1.0

    def test_missing_keywords(self):
        assert missing_keywords("请确认定位状态", ["路径规划", "定位"]) == ["路径规划"]

    def test_passed_default_threshold(self):
        assert keyword_hit_passed("包含a和b", ["a", "b"]) is True
        assert keyword_hit_passed("只包含a", ["a", "b"]) is False

    def test_passed_min_hits(self):
        assert keyword_hit_passed("只包含a", ["a", "b", "c"], min_hits=1) is True
        assert keyword_hit_passed("只有a", ["a", "b"], min_hits=2) is False

    def test_passed_custom_threshold(self):
        assert keyword_hit_passed("只包含a", ["a", "b", "c", "d"], threshold=0.25) is True
