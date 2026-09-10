"""Step4 精排：对接人不 ×2；倾向人保底 0.9；仲裁不截窗。

不调 LLM、不连库。
运行（仓库根）：
    pytest ai/agents/AiDiagnosisPlatform/assigner/tests/test_step4.py -v
"""

from types import SimpleNamespace

from ai.agents.AiDiagnosisPlatform.assigner.ranking.llm_decision import LlmDecision
from ai.agents.AiDiagnosisPlatform.assigner.ranking.ranker import Ranker
from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def _cfg(**kwargs):
    data = dict(
        job_level_penalty={1: 1.0, 2: 0.90, 3: 0.90, 99: 0.90},
        preferred_floor=0.9,
        llm_decision_topk=0,
        department_routing={},
    )
    data.update(kwargs)
    return SimpleNamespace(**data)


def _eng(eid: str, name: str, job_level: int = 1) -> EngineerProfile:
    return EngineerProfile(
        id=eid,
        name=name,
        department="智能规划研究院",
        job_level=job_level,
        responsibility_modules={"摇人吧服务号": {"前端": ["页面"]}},
        duty_text="负责前端",
    )


def _ticket(**kwargs) -> TicketContext:
    data = {
        "id": "t-step4",
        "title": "现场报障",
        "problem_description": "车子停了",
        "status": "new",
    }
    data.update(kwargs)
    return TicketContext(**data)


def _ranked_person(eid: str, total: float, **extra):
    row = {
        "total_score": total, "llm_score": total, "semantic_score": 0.0,
        "history_score": 0.0, "level_multiplier": 1.0, "dept_multiplier": 1.0,
    }
    row.update(extra)
    return eid, row


class TestContactNoDouble:
    """对接人只打标，分数不再 ×2。"""

    def test_contact_same_score_as_peer(self):
        """正常流程：对接人与同召回分的普通人精排总分相同。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 1.0, "u-b": 1.0}
        engineers = [_eng("u-a", "甲"), _eng("u-b", "乙")]
        scores = Ranker(_cfg()).rank(
            recall, engineers=engineers, contact_assignee_id="u-b",
        )
        assert scores["u-b"]["contact_assignee"] is True
        assert scores["u-a"]["total_score"] == scores["u-b"]["total_score"]

    def test_contact_no_floor(self):
        """正常流程：对接人低分不抬到 0.9。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 1.0, "u-b": 0.1}
        engineers = [_eng("u-a", "甲"), _eng("u-b", "乙")]
        scores = Ranker(_cfg()).rank(
            recall, engineers=engineers, contact_assignee_id="u-b",
        )
        assert scores["u-b"]["total_score"] < 0.9
        assert scores["u-b"]["preferred_floor"] is None


class TestPreferredFloor:
    """倾向接单人 max(分, 0.9)，不加倍。"""

    def test_low_score_lifted_to_floor(self):
        """正常流程：倾向人召回分为 0 → 精排抬到 0.9。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 1.0}
        engineers = [_eng("u-a", "甲"), _eng("u-p", "丙")]
        scores = Ranker(_cfg()).rank(
            recall, engineers=engineers, preferred_assignee_id="u-p",
        )
        assert scores["u-p"]["preferred_assignee"] is True
        assert scores["u-p"]["total_score"] == 0.9
        assert scores["u-p"]["preferred_floor"] == 0.9

    def test_high_score_kept(self):
        """正常流程：倾向人加权后已高于 0.9 → 不压低。"""
        recall = RecallResult()
        recall.llm_recall = {"u-p": 1.0, "u-a": 0.1}
        recall.similar_recall = {"u-p": 1.0, "u-a": 0.1}
        engineers = [_eng("u-a", "甲"), _eng("u-p", "丙")]
        scores = Ranker(_cfg()).rank(
            recall, engineers=engineers, preferred_assignee_id="u-p",
        )
        assert scores["u-p"]["total_score"] >= 0.9
        assert scores["u-p"]["total_score"] > scores["u-a"]["total_score"]

    def test_creator_and_prev_no_boost(self):
        """正常流程：提单人 / 原不满意只打标，低分不保底。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 1.0}
        engineers = [_eng("u-a", "甲"), _eng("u-c", "提单"), _eng("u-d", "旧人")]
        scores = Ranker(_cfg()).rank(
            recall, engineers=engineers, creator_id="u-c", prev_assignee_id="u-d",
        )
        assert scores["u-c"]["is_creator"] is True
        assert scores["u-d"]["prev_unsatisfied"] is True
        assert scores["u-c"]["total_score"] < 0.9
        assert scores["u-d"]["total_score"] < 0.9


class TestMisassignRanking:
    """派错纠正：精排压低原处理人。"""

    def test_rejected_total_multiplied(self):
        """正常流程：曾错派人总分 ×0.7，接手人打上转派纠正。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 1.0, "u-b": 1.0}
        recall.misassign_rejected = {"u-a": "不归硬件"}
        recall.misassign_confirmed = {"u-b": "不归硬件"}
        engineers = [_eng("u-a", "甲"), _eng("u-b", "乙")]
        scores = Ranker(_cfg()).rank(recall, engineers=engineers)
        assert scores["u-a"]["misassign_rejected"] is True
        assert scores["u-b"]["misassign_confirmed"] is True
        assert scores["u-a"]["total_score"] == round(scores["u-b"]["total_score"] * 0.70, 4)


class TestNoWindowCut:
    """仲裁窗口：topk<=0 全量；prompt 不再只列 5 人。"""

    def test_window_k_zero_means_all(self):
        """正常流程：llm_decision_topk=0 → 窗口等于人数。"""
        dec = LlmDecision(_cfg(llm_decision_topk=0))
        assert dec._window_k(9) == 9
        assert dec._window_k(0) == 0

    def test_window_k_positive_still_clips(self):
        """兼容：旧配置 topk=3 仍可截。"""
        dec = LlmDecision(_cfg(llm_decision_topk=3))
        assert dec._window_k(9) == 3
        assert dec._window_k(2) == 2

    def test_prompt_lists_all_six(self):
        """正常流程：6 人精排 → prompt 6 人都在，不再 [:5]。"""
        engineers = [_eng(f"u-{i}", f"人{i}") for i in range(6)]
        ranked = dict(
            _ranked_person(f"u-{i}", 0.9 - 0.1 * i) for i in range(6)
        )
        prompt = LlmDecision(_cfg())._build_prompt(
            _ticket(), engineers, RecallResult(), ranked,
        )
        for i in range(6):
            assert f"姓名:人{i} ID:u-{i}" in prompt
        assert "倾向接单人加权" not in prompt
        assert "项目对接人加权" not in prompt

    def test_prompt_preferred_floor_not_contact_bonus(self):
        """正常流程：倾向人展示保底，对接人不写加权。"""
        engineers = [_eng("u-p", "丙"), _eng("u-c", "乙")]
        ranked = {
            "u-p": {
                "preferred_assignee": True, "preferred_floor": 0.9,
                "total_score": 0.9, "llm_score": 0.0, "semantic_score": 0.0,
                "history_score": 0.0, "level_multiplier": 1.0, "dept_multiplier": 1.0,
            },
            "u-c": {
                "contact_assignee": True,
                "total_score": 0.2, "llm_score": 0.2, "semantic_score": 0.0,
                "history_score": 0.0, "level_multiplier": 1.0, "dept_multiplier": 1.0,
            },
        }
        prompt = LlmDecision(_cfg())._build_prompt(
            _ticket(), engineers, RecallResult(), ranked,
        )
        assert "倾向接单人保底≥0.9" in prompt
        assert "项目对接人加权" not in prompt


class TestThreeWayRecall:
    """三路并集：单路保留绝对分，多路取最高。"""

    def test_empty_cluster_does_not_shrink_llm(self):
        """正常流程：问题域空不影响 LLM 分，多路命中取最高。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 1.0, "u-b": 0.2}
        recall.similar_recall = {"u-a": 1.0}
        recall.cluster_recall = {}
        scores = Ranker(_cfg()).rank(
            recall, engineers=[_eng("u-a", "甲"), _eng("u-b", "乙")],
        )
        assert scores["u-a"]["cluster_score"] == 0.0
        assert scores["u-a"]["total_score"] > 0.95
        assert scores["u-a"]["hit_count"] == 2

    def test_similar_absolute_not_stretched(self):
        """正常流程：相似 0.40 不因本批只有他而被拉成 1.0。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 0.80}
        recall.similar_recall = {"u-b": 0.40}
        scores = Ranker(_cfg()).rank(
            recall, engineers=[_eng("u-a", "甲"), _eng("u-b", "乙")],
        )
        assert scores["u-b"]["similar_score"] == 0.40
        assert scores["u-b"]["total_score"] == 0.40

    def test_llm_absolute_not_stretched(self):
        """正常流程：画像 0.85 不按本批第一名拉成 1.0。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 0.85, "u-b": 0.85, "u-c": 0.75}
        engineers = [_eng("u-a", "甲"), _eng("u-b", "乙"), _eng("u-c", "丙"), _eng("u-p", "倾向")]
        scores = Ranker(_cfg()).rank(
            recall, engineers=engineers, preferred_assignee_id="u-p",
        )
        assert scores["u-a"]["llm_score"] == 0.85
        assert scores["u-b"]["llm_score"] == 0.85
        assert scores["u-c"]["llm_score"] == 0.75
        assert scores["u-a"]["total_score"] == 0.85
        assert scores["u-p"]["total_score"] == 0.9
        assert list(scores)[0] == "u-p"

    def test_cluster_only_keeps_full_score(self):
        """正常流程：只在问题域命中 → 保留簇绝对分，不打折。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 1.0}
        recall.cluster_recall = {"u-c": 1.0}
        scores = Ranker(_cfg()).rank(
            recall, engineers=[_eng("u-a", "甲"), _eng("u-c", "丙")],
        )
        assert "u-c" in scores
        assert scores["u-c"]["cluster_score"] == 1.0
        assert scores["u-c"]["llm_score"] == 0.0
        assert scores["u-c"]["total_score"] == scores["u-c"]["cluster_score"]

    def test_both_history_empty_uses_llm_only(self):
        """异常流程：相似和问题域都空 → 只按 LLM，不编历史候选人。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 0.8, "u-b": 0.4}
        scores = Ranker(_cfg()).rank(
            recall, engineers=[_eng("u-a", "甲"), _eng("u-b", "乙")],
        )
        assert scores["u-a"]["similar_score"] == 0.0
        assert scores["u-a"]["cluster_score"] == 0.0
        assert scores["u-a"]["total_score"] > scores["u-b"]["total_score"]
        assert scores["u-a"]["total_score"] == 0.8
        assert scores["u-b"]["total_score"] == 0.4

    def test_example_llm_five_similar_empty_cluster_acf(self):
        """正常流程：LLM=a..e，相似空，簇=a/c/f → 单路保留分，多路取最高，f 不摊权。"""
        recall = RecallResult()
        recall.llm_recall = {"a": 0.90, "b": 0.80, "c": 0.70, "d": 0.60, "e": 0.50}
        recall.similar_recall = {}
        recall.cluster_recall = {"a": 0.80, "c": 1.00, "f": 0.60}
        scores = Ranker(_cfg()).rank(
            recall, engineers=[_eng(x, x.upper()) for x in "abcde"],
        )
        assert scores["f"]["outside_tighten"] is True
        assert scores["f"]["hit_llm"] is False
        assert scores["b"]["hit_cluster"] is False
        assert scores["f"]["total_score"] == scores["f"]["cluster_score"]
        assert scores["a"]["total_score"] == max(
            scores["a"]["llm_score"], scores["a"]["cluster_score"],
        )
        assert scores["c"]["total_score"] == max(
            scores["c"]["llm_score"], scores["c"]["cluster_score"],
        )
        assert list(scores) == ["c", "a", "b", "d", "f", "e"]

    def test_union_marks_outside_tighten(self):
        """正常流程：只在问题域命中、不在收紧名单 → 进精排并标 outside_tighten。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 1.0}
        recall.cluster_recall = {"u-c": 1.0}
        scores = Ranker(_cfg()).rank(
            recall, engineers=[_eng("u-a", "甲")],
        )
        assert "u-c" in scores
        assert scores["u-c"]["outside_tighten"] is True
        assert scores["u-c"]["hit_cluster"] is True
        assert scores["u-a"]["outside_tighten"] is False
        assert scores["u-a"]["hit_llm"] is True
