"""Step6 仲裁：铁律 + 产品附录；失败 / can_decide=false / 名单外 id → None。

不调 LLM、不连库。
运行（仓库根）：
    pytest ai/agents/AiDiagnosisPlatform/assigner/tests/test_step6.py -v
"""

from types import SimpleNamespace

from ai.agents.AiDiagnosisPlatform.assigner.ranking.llm_decision import LlmDecision
from ai.agents.AiDiagnosisPlatform.assigner.ranking.tags import match_engineer_id_strict
from ai.agents.AiDiagnosisPlatform.assigner.recall.recall_result import RecallResult
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def _cfg(**kwargs):
    data = dict(
        ranker_weights={"llm_match": 0.70, "semantic_match": 0.15, "history_match": 0.15},
        job_level_penalty={1: 1.0, 2: 0.90, 3: 0.90, 99: 0.90},
        preferred_floor=0.9,
        llm_decision_topk=0,
        yaorenba_force_module_owner=True,
    )
    data.update(kwargs)
    return SimpleNamespace(**data)


def _eng(eid: str, name: str, duty: str = "负责前端") -> EngineerProfile:
    return EngineerProfile(
        id=eid,
        name=name,
        department="智能规划研究院",
        job_level=1,
        responsibility_modules={"摇人吧服务号": {"前端": ["页面"], "我要摇人": ["入口"]}},
        duty_text=duty,
    )


def _ticket(**kwargs) -> TicketContext:
    data = {
        "id": "t-step6",
        "title": "现场报障",
        "problem_description": "车子停了",
        "status": "new",
    }
    data.update(kwargs)
    return TicketContext(**data)


def _ranked(*ids):
    out = {}
    for i, eid in enumerate(ids):
        out[eid] = {
            "total_score": 0.9 - 0.1 * i, "llm_score": 0.9 - 0.1 * i,
            "semantic_score": 0.0, "history_score": 0.0,
            "level_multiplier": 1.0, "dept_multiplier": 1.0,
        }
    return out


class TestIronRulesPrompt:
    """公共铁律必须写进 prompt。"""

    def test_prompt_has_iron_rules_and_can_decide(self):
        """正常流程：prompt 含铁律、can_decide:false，不含默认回 #1。"""
        prompt = LlmDecision(_cfg())._build_prompt(
            _ticket(), [_eng("u-a", "甲")], RecallResult(), _ranked("u-a"),
        )
        assert "【公共铁律】" in prompt
        assert "【反幻觉】" in prompt
        assert "禁止编造" in prompt
        assert "can_decide:false" in prompt or "can_decide\":false" in prompt
        assert "精确复制" in prompt
        assert "不要因为没把握就默认精排 #1" in prompt
        assert "来源" in prompt
        assert "三路并集" in prompt
        assert "不要拒绝这一选择" in prompt
        assert "页面/显示" not in prompt
        assert "【用户重新派单意图】" not in prompt
        assert "用户指定倾向处理人" not in prompt
        assert "用户重派备注" not in prompt
        assert "名单中带 [倾向接单人]" not in prompt


class TestRedispatchRemarkOnly:
    """重派身份看 Step2 标签；备注有才带一句。"""

    def test_remark_appended_when_present(self):
        """正常流程：有备注才出现「用户重派备注」，不复述倾向人/原处理人。"""
        prompt = LlmDecision(_cfg())._build_prompt(
            _ticket(
                preferred_assignee="u-a",
                prev_assignee="u-b",
                preferred_assignee_remark="请换更熟现场的人",
            ),
            [_eng("u-a", "甲")], RecallResult(), _ranked("u-a"),
        )
        assert "用户重派备注：请换更熟现场的人" in prompt
        assert "名单中带 [倾向接单人] 的是用户勾选" in prompt
        assert "正常情况不要拒绝这一选择" in prompt
        assert "【用户重新派单意图】" not in prompt
        assert "用户指定倾向处理人" not in prompt
        assert "原处理人（用户重派前要换掉的）" not in prompt


class TestProductAppendix:
    """四个产品各有附录；认不出也出未识别附录。"""

    def test_yaorenba_appendix_not_hard_frontend(self):
        """正常流程：摇人吧项目出现产品附录，且写明不硬套前端/后端。"""
        prompt = LlmDecision(_cfg())._build_prompt(
            _ticket(project_name="摇人吧服务号提单", title="我要摇人页面打不开",
                    problem_description="入口进不去"),
            [_eng("u-a", "甲", duty="我要摇人总负责人")],
            RecallResult(),
            _ranked("u-a"),
        )
        assert "【产品附录 · 摇人吧服务号】" in prompt
        assert "不要硬套前端/后端" in prompt
        assert "不是强制派给" in prompt
        assert "姓名:甲 ID:u-a" in prompt

    def test_usp_has_own_appendix(self):
        """正常流程：调度USP 用自己的附录，不再和车端揉成一句占位。"""
        prompt = LlmDecision(_cfg())._build_prompt(
            _ticket(project_name="某某调度USP现场"),
            [_eng("u-a", "甲")], RecallResult(), _ranked("u-a"),
        )
        assert "【产品附录 · 调度USP】" in prompt
        assert "地面侧" in prompt
        assert "本产品暂无额外规则" not in prompt
        assert "【产品附录 · 摇人吧服务号】" not in prompt

    def test_vehicle_software_and_hardware(self):
        """正常流程：车端软件 / 车端硬件各出自己的附录。"""
        soft = LlmDecision(_cfg())._build_prompt(
            _ticket(project_name="某某车端软件现场"),
            [_eng("u-a", "甲")], RecallResult(), _ranked("u-a"),
        )
        hard = LlmDecision(_cfg())._build_prompt(
            _ticket(project_name="某某车端硬件现场"),
            [_eng("u-a", "甲")], RecallResult(), _ranked("u-a"),
        )
        assert "【产品附录 · 车端软件】" in soft
        assert "车上跑的" in soft
        assert "【产品附录 · 车端硬件】" in hard
        assert "能拧的" in hard

    def test_unknown_still_has_appendix(self):
        """正常流程：对不上已知产品也出附录，不再整块缺失。"""
        prompt = LlmDecision(_cfg())._build_prompt(
            _ticket(project_name="内部测试"),
            [_eng("u-a", "甲")], RecallResult(), _ranked("u-a"),
        )
        assert "【产品附录 · 未识别产品】" in prompt
        assert "不要硬套前端/后端" in prompt


class TestParseExits:
    """选中 / can_decide=false / 名单外 / 自造 id。"""

    def test_select_by_id(self):
        """正常流程：精确 id → AssignmentResult。"""
        out = LlmDecision(_cfg())._parse(
            '{"can_decide":true,"engineer_id":"u-a","engineer_name":"甲",'
            '"confidence_score":0.9,"reasoning":"甲最合适","decision_type":"auto"}',
            [_eng("u-a", "甲")],
        )
        assert out is not None
        assert out.engineer_id == "u-a"
        assert out.engineer_name == "甲"
        assert "u-a" not in (out.reasoning or "")

    def test_can_decide_false_is_none(self):
        """异常流程：can_decide=false → None，即使填了人。"""
        out = LlmDecision(_cfg())._parse(
            '{"can_decide":false,"engineer_id":"u-a","reasoning":"不清楚"}',
            [_eng("u-a", "甲")],
        )
        assert out is None

    def test_unknown_id_is_none(self):
        """异常流程：名单外 id → None。"""
        out = LlmDecision(_cfg())._parse(
            '{"engineer_id":"u-ghost","confidence_score":0.9,"decision_type":"auto"}',
            [_eng("u-a", "甲")],
        )
        assert out is None

    def test_name_as_id_is_none(self):
        """数据校验：用姓名当 engineer_id → None。"""
        assert match_engineer_id_strict("甲", [_eng("u-a", "甲")]) is None
        out = LlmDecision(_cfg())._parse(
            '{"engineer_id":"甲","confidence_score":0.9,"decision_type":"auto"}',
            [_eng("u-a", "甲")],
        )
        assert out is None


class TestRecallSourceOnPrompt:
    """并集候选人要把命中哪几路带给仲裁。"""

    def test_prompt_lists_path_hits_and_outside_tighten(self):
        """正常流程：来源行含 LLM / 问题域，以及历史捞回说明。"""
        ranked = {
            "u-a": {
                "total_score": 0.9, "llm_score": 1.0, "similar_score": 0.0,
                "cluster_score": 0.0, "hit_llm": True, "hit_similar": False,
                "hit_cluster": False, "outside_tighten": False,
                "level_multiplier": 1.0, "dept_multiplier": 1.0,
            },
            "u-c": {
                "total_score": 0.15, "llm_score": 0.0, "similar_score": 0.0,
                "cluster_score": 1.0, "hit_llm": False, "hit_similar": False,
                "hit_cluster": True, "outside_tighten": True,
                "level_multiplier": 1.0, "dept_multiplier": 1.0,
            },
        }
        prompt = LlmDecision(_cfg())._build_prompt(
            _ticket(), [_eng("u-a", "甲"), _eng("u-c", "丙")], RecallResult(), ranked,
        )
        assert "来源: LLM" in prompt
        assert "来源: 问题域" in prompt
        assert "不在部门/产品收紧名单，由历史捞回" in prompt
        assert "LLM未召回" in prompt


class TestNoTop1Fallback:
    """窗口空 / 调用失败 本层不造精排 #1。"""

    def test_empty_window_returns_none(self):
        """异常流程：窗口无人 → None。"""
        import asyncio
        out = asyncio.run(LlmDecision(_cfg()).adecide(
            _ticket(), [_eng("u-a", "甲")], RecallResult(), {},
        ))
        assert out is None
