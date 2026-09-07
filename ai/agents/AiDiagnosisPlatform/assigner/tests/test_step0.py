"""Step0 指定人支路：与重派倾向人同一出口。

不调 LLM、不连库。强信号走「指定处理人：X」；全量用户兜底用 mock。
运行（仓库根）：
    pytest ai/agents/AiDiagnosisPlatform/assigner/tests/test_step0.py -v
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow import DispatchFlow
from ai.agents.AiDiagnosisPlatform.assigner.schemas import EngineerProfile, TicketContext


def _ticket(title: str) -> TicketContext:
    return TicketContext(
        id="t-step0",
        title=title,
        problem_description="现场报障",
        status="new",
    )


def _complete(name: str = "张三", eid: str = "u-zhang") -> EngineerProfile:
    return EngineerProfile(
        id=eid,
        name=name,
        department="智能规划研究院",
        job_level=1,
        responsibility_modules={"摇人吧服务号": {"前端": ["页面"]}},
        duty_text="负责前端",
    )


def _incomplete(name: str = "张三", eid: str = "u-zhang") -> EngineerProfile:
    return EngineerProfile(id=eid, name=name)


def _run_detect(title: str, engineers, everyone=None):
    flow = DispatchFlow()

    async def _go():
        ctx = _ticket(title)
        if everyone is False:
            with patch.object(DispatchFlow, "_match_preferred_everyone", return_value=None):
                return await flow._detect_preferred_assignee(ctx, engineers)
        return await flow._detect_preferred_assignee(ctx, engineers)

    return asyncio.run(_go())


class TestStep0DetectPreferred:
    """强信号「指定处理人：X」，写入 preferred_id / matched_pref / pinyin_match / profile。"""

    def test_hit_complete_assigns_as_preferred(self):
        """正常流程：人在且画像完整 → 派此人，preferred_id 对齐，无 tip 字段。"""
        result, unresolved = _run_detect("指定处理人：张三", [_complete()])
        assert result is not None
        assert result.engineer_id == "u-zhang"
        assert result.preferred_id == "u-zhang"
        assert result.matched_pref is True
        assert result.pinyin_match is False
        assert not (result.profile or {}).get("missing")
        assert unresolved is None

    def test_hit_incomplete_keeps_missing(self):
        """正常流程：人在但画像不完整 → 照样派，profile.missing 给 GET 拼 tip。"""
        result, unresolved = _run_detect("指定处理人：张三", [_incomplete()])
        assert result is not None
        assert result.preferred_id == "u-zhang"
        assert result.matched_pref is True
        assert (result.profile or {}).get("missing")
        assert unresolved is None

    def test_pinyin_sets_flag(self):
        """正常流程：拼音全拼命中 → 派命中人，pinyin_match=True。"""
        flow = DispatchFlow()
        if not flow._to_pinyin("胡建南"):
            pytest.skip("pypinyin 不可用，跳过拼音用例")
        result, unresolved = _run_detect(
            "指定处理人：胡建南", [_complete("胡健楠", "u-hu")],
        )
        assert result is not None
        assert result.engineer_id == "u-hu"
        assert result.preferred_id == "u-hu"
        assert result.matched_pref is True
        assert result.pinyin_match is True
        assert unresolved is None

    def test_not_found_continues_with_specified_name(self):
        """异常流程：指定人找不到 → 不卡住，返回指定名供 profile.specified_name。"""
        result, unresolved = _run_detect(
            "指定处理人：赵不存在", [_complete()], everyone=False,
        )
        assert result is None
        assert unresolved == "赵不存在"

    def test_no_specified_person(self):
        """正常流程：未指定人 → 继续智能派单，无指定名。"""
        result, unresolved = _run_detect("车辆定位漂移", [_complete()])
        assert result is None
        assert unresolved is None


class TestStep0TipOutlet:
    """GET 出口：build_redispatch_tip 读同一组日志字段。"""

    def test_complete_no_tip(self):
        """正常流程：已派到指定人且画像完整 → 不提醒。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-zhang",
            preferred_id="u-zhang",
            pinyin_match=False,
            name_collision=False,
            profile={"dept": "智能规划研究院", "missing": []},
        )
        assert build_redispatch_tip(log, {"u-zhang": "张三"}) is None

    def test_incomplete_tip(self):
        """正常流程：已派到指定人但画像不完整。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-zhang",
            preferred_id="u-zhang",
            pinyin_match=False,
            name_collision=False,
            profile={"missing": ["department"]},
        )
        assert build_redispatch_tip(log, {"u-zhang": "张三"}) == "您指定的接单人画像不完整，请补充"

    def test_pinyin_tip(self):
        """正常流程：拼音命中。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-hu",
            preferred_id="u-hu",
            pinyin_match=True,
            name_collision=False,
            profile={"missing": []},
        )
        assert build_redispatch_tip(log, {"u-hu": "胡健楠"}) == "拼音找到的是【胡健楠】，有可能不准确"

    def test_specified_not_found_tip(self):
        """异常流程：指定人找不到，只有 specified_name。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-other",
            preferred_id=None,
            pinyin_match=False,
            name_collision=False,
            profile={"specified_name": "赵不存在", "missing": []},
        )
        assert (
            build_redispatch_tip(log, {"u-other": "李四"})
            == "没找到您指定的【赵不存在】，已按智能派单处理"
        )

    def test_redispatch_unmatched_still_works(self):
        """正常流程：重派未派到倾向人，旧出口不变。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-li",
            preferred_id="u-zhang",
            pinyin_match=False,
            name_collision=False,
            profile={"missing": []},
        )
        assert (
            build_redispatch_tip(log, {"u-zhang": "张三", "u-li": "李四"})
            == "很抱歉，您指定的【张三】暂未采纳，已改派更合适的【李四】处理"
        )

    def test_collision_random_and_missing_tip(self):
        """正常流程：同名随机 + 画像缺项 → 提醒随机选了谁，并请补充画像。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-1",
            preferred_id="u-1",
            pinyin_match=False,
            name_collision=True,
            profile={
                "missing": ["department", "responsibility_modules"],
                "collision_random": True,
            },
        )
        assert (
            build_redispatch_tip(log, {"u-1": "张三"})
            == "指派人存在同名，已随机选择【张三】；您指定的接单人画像不完整，请补充"
        )


def _run_pick(matches, llm_json: str, pick=None):
    flow = DispatchFlow()

    async def _go():
        llm = SimpleNamespace(complete=AsyncMock(return_value=llm_json))
        ctx = _ticket("指定处理人：张三")
        with patch("ai.core.get_llm_client", AsyncMock(return_value=llm)):
            if pick is None:
                return await flow._pick_collision(ctx, "张三", matches)
            with patch(
                "ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow.random.choice",
                return_value=pick,
            ):
                return await flow._pick_collision(ctx, "张三", matches)

    return asyncio.run(_go())


class TestPickCollisionCanDetermine:
    """同名 LLM：can_determine:false 才是「无法区分」。"""

    def test_can_determine_false_random(self):
        """异常流程：模型按 prompt 输出 false → 随机选，不再当选中失败。"""
        a, b = _complete("张三", "u-1"), _complete("张三", "u-2")
        winner, reason, rnd = _run_pick([a, b], '{"can_determine": false}', pick=b)
        assert winner.id == "u-2"
        assert "无法区分" in reason
        assert rnd is True

    def test_selected_id_still_wins(self):
        """正常流程：给出名单内 selected_id → 派此人。"""
        a, b = _complete("张三", "u-1"), _complete("张三", "u-2")
        winner, reason, rnd = _run_pick(
            [a, b], '{"selected_id":"u-2","reason":"更熟现场"}',
        )
        assert winner.id == "u-2"
        assert reason == "更熟现场"
        assert rnd is False
