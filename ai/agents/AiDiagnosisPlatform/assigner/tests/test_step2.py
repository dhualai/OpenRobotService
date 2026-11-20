"""Step2 打标：四标签 + 候选快照 + 模糊强信号（词表空不命中）。

不调 LLM、不连库。精排规则见 test_step4.py；模糊截断接通见 test_step7.py。
运行（仓库根）：
    pytest ai/agents/AiDiagnosisPlatform/assigner/tests/test_step2.py -v
"""

from types import SimpleNamespace

from ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow import _candidates_snapshot
from ai.agents.AiDiagnosisPlatform.assigner.ranking.llm_decision import LlmDecision
from ai.agents.AiDiagnosisPlatform.assigner.ranking.ranker import Ranker
from ai.agents.AiDiagnosisPlatform.assigner.ranking.tags import (
    TAG_CONTACT,
    TAG_CREATOR,
    TAG_PREFERRED,
    TAG_PREV_UNSATISFIED,
    llm_person_label,
    match_engineer_from_llm,
    match_vague_strong_signal,
    score_tag_labels,
)
from ai.agents.AiDiagnosisPlatform.assigner.recall.llm_recall import LlmRecall
from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def _eng(eid: str, name: str) -> EngineerProfile:
    return EngineerProfile(
        id=eid,
        name=name,
        department="智能规划研究院",
        job_level=1,
        responsibility_modules={"摇人吧服务号": {"前端": ["页面"]}},
        duty_text="负责前端",
    )


def _cfg():
    return SimpleNamespace(
        job_level_penalty={1: 1.0, 2: 0.90, 3: 0.90, 99: 0.90},
        preferred_floor=0.9,
        llm_decision_topk=0,
    )


def _ticket(**kwargs) -> TicketContext:
    data = {
        "id": "t-step2",
        "title": "现场报障",
        "problem_description": "车子停了",
        "status": "new",
    }
    data.update(kwargs)
    return TicketContext(**data)


class TestScoreTagLabels:
    """精排字段 → 本版标签名。"""

    def test_four_tags_in_order(self):
        """正常流程：四人身份齐全 → 提单人 / 对接人 / 原不满意 / 倾向接单人。"""
        labels = score_tag_labels({
            "is_creator": True,
            "contact_assignee": True,
            "prev_unsatisfied": True,
            "preferred_assignee": True,
        })
        assert labels == [TAG_CREATOR, TAG_CONTACT, TAG_PREV_UNSATISFIED, TAG_PREFERRED]

    def test_old_names_gone(self):
        """数据校验：不再出现自提单人 / 上次倾向 / 用户倾向。"""
        labels = score_tag_labels({
            "is_creator": True,
            "preferred_assignee": True,
        })
        joined = " ".join(labels)
        assert "自提单人" not in joined
        assert "上次倾向" not in joined
        assert "用户倾向" not in joined
        assert TAG_CREATOR in labels
        assert TAG_PREFERRED in labels

    def test_empty_score_no_tags(self):
        """异常流程：空分数字典 → 无标签。"""
        assert score_tag_labels({}) == []
        assert score_tag_labels(None) == []


class TestRankerTags:
    """Ranker 四身份打标（分数规则见 test_step4）。"""

    def test_flags_on_four_roles(self):
        """正常流程：四身份分别打上 is_creator / contact / preferred / prev_unsatisfied。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 0.8, "u-b": 0.7, "u-c": 0.6, "u-d": 0.5}
        engineers = [
            _eng("u-a", "甲"),
            _eng("u-b", "乙"),
            _eng("u-c", "丙"),
            _eng("u-d", "丁"),
        ]
        scores = Ranker(_cfg()).rank(
            recall,
            engineers=engineers,
            creator_id="u-a",
            contact_assignee_id="u-b",
            preferred_assignee_id="u-c",
            prev_assignee_id="u-d",
        )
        assert scores["u-a"]["is_creator"] is True
        assert scores["u-b"]["contact_assignee"] is True
        assert scores["u-c"]["preferred_assignee"] is True
        assert scores["u-d"]["prev_unsatisfied"] is True
        assert score_tag_labels(scores["u-a"]) == [TAG_CREATOR]
        assert score_tag_labels(scores["u-b"]) == [TAG_CONTACT]
        assert score_tag_labels(scores["u-c"]) == [TAG_PREFERRED]
        assert score_tag_labels(scores["u-d"]) == [TAG_PREV_UNSATISFIED]

    def test_prev_outside_candidates_not_injected(self):
        """边界：原接单人不在候选 → Ranker 不注入；流程也不强制加回。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 0.9}
        engineers = [_eng("u-a", "甲")]
        scores = Ranker(_cfg()).rank(
            recall,
            engineers=engineers,
            prev_assignee_id="u-ghost",
        )
        assert "u-ghost" not in scores

    def test_prev_in_candidates_kept_even_if_not_recalled(self):
        """正常流程：原接单人在候选但三路未召回 → 仍进精排并打标。"""
        recall = RecallResult()
        recall.llm_recall = {"u-a": 0.9}
        engineers = [_eng("u-a", "甲"), _eng("u-prev", "旧人")]
        scores = Ranker(_cfg()).rank(
            recall,
            engineers=engineers,
            prev_assignee_id="u-prev",
        )
        assert scores["u-prev"]["prev_unsatisfied"] is True
        assert TAG_PREV_UNSATISFIED in score_tag_labels(scores["u-prev"])


class TestForceKeepEngineer:
    """Step2.5–2.6 从准入池加回（对接人/倾向人）。"""

    def test_append_when_missing_in_candidates(self):
        """正常流程：池里有、候选没有 → append。"""
        from ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow import DispatchFlow

        pool = [_eng("u-a", "甲"), _eng("u-prev", "旧人")]
        cands = [_eng("u-a", "甲")]
        kept = DispatchFlow._force_keep_engineer(cands, pool, "u-prev")
        assert kept is not None and kept.id == "u-prev"
        assert any(e.id == "u-prev" for e in cands)

    def test_noop_when_already_present_or_not_in_pool(self):
        """边界：已在候选 / 池中无此人 → None。"""
        from ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow import DispatchFlow

        pool = [_eng("u-a", "甲")]
        cands = [_eng("u-a", "甲")]
        assert DispatchFlow._force_keep_engineer(cands, pool, "u-a") is None
        assert DispatchFlow._force_keep_engineer(cands, pool, "u-ghost") is None
        assert DispatchFlow._force_keep_engineer(cands, pool, None) is None


class TestCandidatesSnapshot:
    """候选快照 tags 与精排字段对齐。"""

    def test_snapshot_carries_four_tags(self):
        """正常流程：快照 tags 为新标签名，不含旧名。"""
        engineers = [
            _eng("u-a", "甲"),
            _eng("u-b", "乙"),
            _eng("u-c", "丙"),
            _eng("u-d", "丁"),
        ]
        ranked = {
            "u-a": {"is_creator": True, "total_score": 0.9},
            "u-b": {"contact_assignee": True, "total_score": 0.8},
            "u-c": {"preferred_assignee": True, "total_score": 0.7},
            "u-d": {"prev_unsatisfied": True, "total_score": 0.6},
        }
        shot = _candidates_snapshot(ranked, engineers, topk=10)
        by_id = {row["engineer_id"]: row["tags"] for row in shot}
        assert by_id["u-a"] == [TAG_CREATOR]
        assert by_id["u-b"] == [TAG_CONTACT]
        assert by_id["u-c"] == [TAG_PREFERRED]
        assert by_id["u-d"] == [TAG_PREV_UNSATISFIED]
        flat = " ".join(t for tags in by_id.values() for t in tags)
        assert "自提单人" not in flat
        assert "上次倾向" not in flat
        assert "用户倾向" not in flat


class TestLlmPromptTags:
    """标签进 Step6 prompt，中途不丢。"""

    def test_prompt_uses_new_tag_names(self):
        """正常流程：仲裁 prompt 含新标签，不含自提单人/用户倾向。"""
        engineers = [
            _eng("u-a", "甲"),
            _eng("u-b", "乙"),
            _eng("u-c", "丙"),
            _eng("u-d", "丁"),
        ]
        ranked = {
            "u-a": {
                "is_creator": True, "total_score": 0.9,
                "llm_score": 0.9, "semantic_score": 0.0, "history_score": 0.0,
                "level_multiplier": 1.0, "dept_multiplier": 1.0,
            },
            "u-b": {
                "contact_assignee": True, "total_score": 0.8,
                "llm_score": 0.8, "semantic_score": 0.0, "history_score": 0.0,
                "level_multiplier": 1.0, "dept_multiplier": 1.0,
            },
            "u-c": {
                "preferred_assignee": True, "total_score": 0.7,
                "llm_score": 0.7, "semantic_score": 0.0, "history_score": 0.0,
                "level_multiplier": 1.0, "dept_multiplier": 1.0,
            },
            "u-d": {
                "prev_unsatisfied": True, "total_score": 0.6,
                "llm_score": 0.6, "semantic_score": 0.0, "history_score": 0.0,
                "level_multiplier": 1.0, "dept_multiplier": 1.0,
            },
        }
        prompt = LlmDecision(_cfg())._build_prompt(
            _ticket(), engineers, RecallResult(), ranked,
        )
        assert "[提单人]" in prompt
        assert "[项目对接人]" in prompt
        assert "[倾向接单人]" in prompt
        assert "[原用户不满意的接单人]" in prompt
        assert "[自提单人]" not in prompt
        assert "[用户倾向]" not in prompt
        assert "姓名:甲 ID:u-a" in prompt
        assert "姓名:乙 ID:u-b" in prompt
        assert "姓名:丙 ID:u-c" in prompt
        assert "姓名:丁 ID:u-d" in prompt
        assert "engineer_name" in prompt

    def test_redispatch_people_are_id_and_name(self):
        """正常流程：倾向人 / 原接单人在 prompt 里也是 姓名 + ID。"""
        engineers = [_eng("u-c", "丙"), _eng("u-d", "丁")]
        ranked = {
            "u-c": {
                "preferred_assignee": True, "total_score": 0.7,
                "llm_score": 0.7, "semantic_score": 0.0, "history_score": 0.0,
                "level_multiplier": 1.0, "dept_multiplier": 1.0,
            },
            "u-d": {
                "prev_unsatisfied": True, "total_score": 0.6,
                "llm_score": 0.6, "semantic_score": 0.0, "history_score": 0.0,
                "level_multiplier": 1.0, "dept_multiplier": 1.0,
            },
        }
        prompt = LlmDecision(_cfg())._build_prompt(
            _ticket(preferred_assignee="u-c", prev_assignee="u-d"),
            engineers, RecallResult(), ranked,
        )
        assert "姓名:丙 ID:u-c" in prompt
        assert "姓名:丁 ID:u-d" in prompt

    def test_parse_accepts_name_or_full_label(self):
        """正常流程：决策 JSON 填 id 或整段标签能对上人；只填姓名视为自造 → None。"""
        engineers = [_eng("u-a", "甲"), _eng("u-b", "乙")]
        dec = LlmDecision(_cfg())
        by_id = dec._parse(
            '{"engineer_id":"u-a","engineer_name":"甲","confidence_score":0.9,"reasoning":"甲最合适","decision_type":"auto"}',
            engineers,
        )
        assert by_id is not None and by_id.engineer_id == "u-a" and by_id.engineer_name == "甲"
        by_label = dec._parse(
            '{"engineer_id":"姓名:乙 ID:u-b","confidence_score":0.8,"reasoning":"乙","decision_type":"auto"}',
            engineers,
        )
        assert by_label is not None and by_label.engineer_id == "u-b"
        by_name = dec._parse(
            '{"engineer_id":"甲","confidence_score":0.7,"reasoning":"甲","decision_type":"auto"}',
            engineers,
        )
        assert by_name is None


class TestLlmPersonLabel:
    """给大模型看的人员格式：姓名在前。"""

    def test_id_and_name(self):
        """正常流程：有 id 有姓名 → 姓名:x ID:y。"""
        assert llm_person_label(eng=_eng("u-a", "甲")) == "姓名:甲 ID:u-a"
        assert llm_person_label("u-a", "甲") == "姓名:甲 ID:u-a"
        assert match_engineer_from_llm("u-a", [_eng("u-a", "甲")]) is not None

    def test_missing_sides_get_placeholder(self):
        """边界：缺 id / 缺姓名仍两边都在。"""
        assert llm_person_label(None, "甲") == "姓名:甲 ID:?"
        assert llm_person_label("u-a", "") == "姓名:未知 ID:u-a"


class TestL1PromptPerson:
    """L1 候选列表同样是 姓名 + ID。"""

    def test_l1_prompt_has_id_and_name(self):
        """正常流程：L1 prompt 含 姓名: 和 ID:，不再只写候选ID。"""
        prompt = LlmRecall(_cfg())._build_prompt(_ticket(), [_eng("u-a", "甲")], top_k=1)
        assert "姓名:甲 ID:u-a" in prompt
        assert "候选ID: u-a" not in prompt


class TestVagueStrongSignal:
    """只认 dispatch_hint=severe；描述正文不截断。"""

    def test_description_marker_no_longer_hits(self):
        """正常流程：描述里写 [问题描述不完整] 不再截断。"""
        cfg = SimpleNamespace(vague_strong_signals={"enabled": True})
        assert match_vague_strong_signal(
            _ticket(problem_description="车子停了\n[问题描述不完整]"), cfg,
        ) is False

    def test_missing_config_no_match(self):
        """异常流程：未配 vague_strong_signals → False。"""
        ticket = _ticket(problem_description="帮我看一下")
        assert match_vague_strong_signal(ticket, SimpleNamespace()) is False

    def test_dispatch_hint_only_severe_skips(self):
        """正常流程：severe 截断；lacking 不截断（仍走正常召回）。"""
        cfg = SimpleNamespace(vague_strong_signals={"enabled": True})
        assert match_vague_strong_signal(
            _ticket(dispatch_hint="severe"), cfg,
        ) is True
        assert match_vague_strong_signal(
            _ticket(dispatch_hint="lacking"), cfg,
        ) is False
        assert match_vague_strong_signal(
            _ticket(dispatch_hint=""), cfg,
        ) is False

    def test_dispatch_hint_disabled(self):
        """权限/开关：enabled=false → severe 也不截断。"""
        cfg = SimpleNamespace(vague_strong_signals={"enabled": False})
        assert match_vague_strong_signal(
            _ticket(dispatch_hint="severe"), cfg,
        ) is False
