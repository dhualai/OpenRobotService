"""DispatchFlow 辅助函数：画像字典 / 候选快照 / 同名检测 / 画像完整性。

不调 LLM、不连库。
运行（仓库根）：
    pytest ai/agents/AiDiagnosisPlatform/assigner/tests/test_helpers.py -v
"""

from ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow import (
    _engineer_profile_dict,
    _step0_winner_profile,
    _candidates_snapshot,
    _profile_has_any,
    _dup_names,
)
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile


def _eng(
    eid="u1", name="张三", dept="智能规划研究院",
    job_level=1, modules=None, duty="负责前端",
) -> EngineerProfile:
    return EngineerProfile(
        id=eid, name=name, department=dept, job_level=job_level,
        responsibility_modules=modules or {"调度USP": {"监控": ["路径规划"]}},
        duty_text=duty,
    )


class TestEngineerProfileDict:
    """_engineer_profile_dict：画像字典 + missing 判定。"""

    def test_complete_profile_no_missing(self):
        eng = _eng()
        d = _engineer_profile_dict(eng)
        assert d["dept"] == "智能规划研究院"
        assert d["job_level"] == 1
        assert d["modules"] == ["路径规划"]
        assert d["duty"] == "负责前端"
        assert d["missing"] == []

    def test_missing_department(self):
        eng = EngineerProfile(id="u1", name="张三", job_level=1,
                              responsibility_modules={"调度USP": {"监控": ["路径规划"]}})
        d = _engineer_profile_dict(eng)
        assert "department" in d["missing"]

    def test_missing_job_level(self):
        """异常流程：job_level=0 视为缺失。"""
        eng = EngineerProfile(id="u1", name="张三", department="dept",
                              job_level=0,
                              responsibility_modules={"调度USP": {"监控": ["路径规划"]}})
        d = _engineer_profile_dict(eng)
        assert "job_level" in d["missing"]

    def test_missing_responsibility_modules(self):
        """异常流程：responsibility_modules 空 → 缺失。"""
        eng = EngineerProfile(id="u1", name="张三", department="dept", job_level=1)
        d = _engineer_profile_dict(eng)
        assert "responsibility_modules" in d["missing"]

    def test_all_missing(self):
        eng = EngineerProfile(id="u1", name="张三", job_level=0)
        d = _engineer_profile_dict(eng)
        assert set(d["missing"]) == {"department", "job_level", "responsibility_modules"}


class TestStep0WinnerProfile:
    """_step0_winner_profile：Step0 落库画像特殊字段。"""

    def test_basic(self):
        eng = _eng()
        prof = _step0_winner_profile(eng)
        assert prof["dept"] == "智能规划研究院"
        assert prof["missing"] == []

    def test_collision_random_flag(self):
        eng = _eng()
        prof = _step0_winner_profile(eng, collision_random=True)
        assert prof["collision_random"] is True

    def test_specified_name_when_different(self):
        """正常流程：用户原文与工程师名不同时写入 specified_name。"""
        eng = _eng(name="贾爽")
        prof = _step0_winner_profile(eng, specified_name="加双")
        assert prof["specified_name"] == "加双"

    def test_specified_name_not_written_when_same(self):
        """正常流程：用户原文与工程师名相同时不写 specified_name。"""
        eng = _eng(name="张三")
        prof = _step0_winner_profile(eng, specified_name="张三")
        assert "specified_name" not in prof

    def test_specified_multi_flag(self):
        eng = _eng()
        prof = _step0_winner_profile(eng, specified_multi=True)
        assert prof["specified_multi"] is True


class TestCandidatesSnapshot:
    """_candidates_snapshot：候选快照导出 + 兜底补齐。"""

    def test_ranked_scores_populate(self):
        """正常流程：精排分按排名导出。"""
        eng1 = _eng("u1", "甲")
        eng2 = _eng("u2", "乙")
        scores = {
            "u1": {"total_score": 0.9, "llm_score": 0.9, "similar_score": 0.0,
                    "history_score": 0.0, "level_multiplier": 1.0, "dept_multiplier": 1.0},
            "u2": {"total_score": 0.8, "llm_score": 0.8, "similar_score": 0.0,
                    "history_score": 0.0, "level_multiplier": 1.0, "dept_multiplier": 1.0},
        }
        shot = _candidates_snapshot(scores, [eng1, eng2], topk=10)
        assert len(shot) == 2
        assert shot[0]["rank"] == 1
        assert shot[0]["engineer_id"] == "u1"
        assert shot[1]["rank"] == 2
        assert shot[1]["engineer_id"] == "u2"

    def test_topk_limit(self):
        """正常流程：超过 topk 截断。"""
        engs = [_eng(f"u{i}", f"名{i}") for i in range(15)]
        scores = {f"u{i}": {"total_score": 1.0 - i * 0.01} for i in range(15)}
        shot = _candidates_snapshot(scores, engs, topk=5)
        assert len(shot) == 5

    def test_empty_scores_uses_candidates_fallback(self):
        """正常流程：精排空 → 从候选人兜底补齐。"""
        engs = [_eng("u1", "甲"), _eng("u2", "乙")]
        shot = _candidates_snapshot({}, engs, topk=10)
        assert len(shot) == 2
        assert shot[0]["rank"] == 1
        assert shot[0]["engineer_id"] == "u1"

    def test_partial_scores_filled_with_candidates(self):
        """正常流程：精排不足 topk → 从候选人补齐。"""
        engs = [_eng("u1", "甲"), _eng("u2", "乙"), _eng("u3", "丙")]
        scores = {"u1": {"total_score": 0.9, "llm_score": 0.9,
                          "similar_score": 0, "history_score": 0,
                          "level_multiplier": 1.0, "dept_multiplier": 1.0}}
        shot = _candidates_snapshot(scores, engs, topk=10)
        assert len(shot) == 3
        assert shot[0]["engineer_id"] == "u1"
        assert shot[1]["rank"] == 2

    def test_profile_with_any_priority_in_fallback(self):
        """正常流程：兜底时有画像的排前面。"""
        eng_complete = _eng("u1", "甲")
        eng_bare = EngineerProfile(id="u2", name="乙", job_level=0)
        shot = _candidates_snapshot({}, [eng_bare, eng_complete], topk=10)
        assert shot[0]["engineer_id"] == "u1"
        assert shot[1]["engineer_id"] == "u2"

    def test_scores_in_candidate_dict(self):
        """正常流程：精排字段正确映射到 scores 子字典。"""
        eng = _eng("u1", "甲")
        scores = {"u1": {"total_score": 0.85, "llm_score": 0.9,
                          "similar_score": 0.3, "history_score": 0.3,
                          "level_multiplier": 1.0, "dept_multiplier": 0.95}}
        shot = _candidates_snapshot(scores, [eng], topk=10)
        assert shot[0]["scores"]["llm"] == 0.9
        assert shot[0]["scores"]["similar"] == 0.3
        assert shot[0]["scores"]["total"] == 0.85


class TestProfileHasAny:
    """_profile_has_any：画像非空判定。"""

    def test_with_department(self):
        eng = EngineerProfile(id="u1", name="张三", department="dept")
        assert _profile_has_any(eng)

    def test_with_job_level(self):
        eng = EngineerProfile(id="u1", name="张三", job_level=2)
        assert _profile_has_any(eng)

    def test_with_modules(self):
        eng = EngineerProfile(id="u1", name="张三",
                              responsibility_modules={"调度USP": ["路径规划"]})
        assert _profile_has_any(eng)
        eng = EngineerProfile(id="u1", name="张三",
                              responsibility_modules={"调度USP": ["路径规划"]})
        assert _profile_has_any(eng)

    def test_bare_profile_false(self):
        """异常流程：无部门/职级/模块 → 无画像。"""
        eng = EngineerProfile(id="u1", name="张三", job_level=0)
        assert not _profile_has_any(eng)

    def test_whitespace_department_false(self):
        eng = EngineerProfile(id="u1", name="张三", department="  ", job_level=0)
        assert not _profile_has_any(eng)


class TestDupNames:
    """_dup_names：同名检测。"""

    def test_no_duplicates(self):
        engs = [_eng("u1", "甲"), _eng("u2", "乙")]
        assert _dup_names(engs) == set()

    def test_duplicates_found(self):
        engs = [_eng("u1", "张三"), _eng("u2", "张三"), _eng("u3", "李四")]
        assert _dup_names(engs) == {"张三"}

    def test_empty_name_not_counted(self):
        engs = [EngineerProfile(id="u1", name=""), EngineerProfile(id="u2", name="")]
        assert _dup_names(engs) == set()

    def test_multiple_dup_names(self):
        engs = [
            _eng("u1", "张三"), _eng("u2", "张三"),
            _eng("u3", "李四"), _eng("u4", "李四"),
        ]
        assert _dup_names(engs) == {"张三", "李四"}
