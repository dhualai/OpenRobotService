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


def _ticket(title: str, description: str = "现场报障") -> TicketContext:
    return TicketContext(
        id="t-step0",
        title=title,
        problem_description=description,
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


def _run_detect(title: str, engineers, everyone=None, description: str = "现场报障"):
    flow = DispatchFlow()

    async def _go():
        ctx = _ticket(title, description)
        if everyone is False:
            with patch.object(DispatchFlow, "_match_preferred_everyone", return_value=None):
                return await flow._detect_preferred_assignee(ctx, engineers)
        if isinstance(everyone, tuple):
            with patch.object(DispatchFlow, "_match_preferred_everyone", return_value=everyone):
                return await flow._detect_preferred_assignee(ctx, engineers)
        return await flow._detect_preferred_assignee(ctx, engineers)

    return asyncio.run(_go())


def _run_weak(description: str, engineers, llm_json: str, everyone=None):
    flow = DispatchFlow()

    async def _go():
        ctx = _ticket("车辆定位漂移", description)
        llm = SimpleNamespace(complete=AsyncMock(return_value=llm_json))
        everyone_patch = everyone
        with patch("ai.core.get_llm_client", AsyncMock(return_value=llm)):
            if everyone is False:
                with patch.object(DispatchFlow, "_match_preferred_everyone", return_value=None):
                    return await flow._detect_preferred_assignee(ctx, engineers)
            if isinstance(everyone_patch, tuple):
                with patch.object(DispatchFlow, "_match_preferred_everyone", return_value=everyone_patch):
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
        """正常流程：指定「加双」拼音命中「贾爽」，原文进 specified_name。"""
        flow = DispatchFlow()
        if not flow._to_pinyin("加双") or flow._to_pinyin("加双") != flow._to_pinyin("贾爽"):
            pytest.skip("pypinyin 不可用或加双/贾爽读音不同")
        result, unresolved = _run_detect(
            "指定处理人：加双", [_complete("贾爽", "u-jia")],
        )
        assert result is not None
        assert result.engineer_id == "u-jia"
        assert result.preferred_id == "u-jia"
        assert result.matched_pref is True
        assert result.pinyin_match is True
        assert (result.profile or {}).get("specified_name") == "加双"
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


class TestStep0StrongPhrases:
    """强信号文案：提单 Agent 写入描述的几种写法都要抽出人名。"""

    def test_extract_variants(self):
        """正常流程：指定处理人 / 指定人 / 半角冒号 / 方括号。"""
        extract = DispatchFlow._extract_strong_preferred
        assert extract("指定处理人：张三") == "张三"
        assert extract("指定处理人:张三") == "张三"
        assert extract("[指定处理人：张三] 现场报障") == "张三"
        assert extract("指定人：李四") == "李四"
        assert extract("指定人员：王五") == "王五"
        assert extract("车辆定位漂移") is None
        assert extract("转给张三") is None

    def test_in_description_not_title(self):
        """正常流程：人名写在描述里（提单 Agent 实际落点）也能命中。"""
        result, unresolved = _run_detect(
            "虚拟车任务不执行", [_complete()],
            description="[指定处理人：张三] 任务停留在下发阶段",
        )
        assert result is not None
        assert result.engineer_id == "u-zhang"
        assert unresolved is None


class TestStep0WeakPhrases:
    """弱信号：转给 / 给…看一下 等，走 LLM 抽人名，tip 出口与强信号相同。"""

    def test_maybe_has_preferred_triggers(self):
        """正常流程：常见口语能进弱信号；纯报障不进。"""
        maybe = DispatchFlow._maybe_has_preferred
        assert maybe("转给张三")
        assert maybe("这个给张三看一下")
        assert maybe("让李四处理")
        assert maybe("请王五来处理")
        assert maybe("派给赵六")
        assert maybe("安排给钱七")
        assert maybe("找周八跟进")
        assert not maybe("车辆定位漂移，任务下发失败")

    def test_weak_hit_assigns(self):
        """正常流程：转给张三 → LLM 抽出姓名 → 派此人。"""
        result, unresolved = _run_weak(
            "这个问题转给张三", [_complete()],
            '{"has_preference": true, "preferred_name": "张三"}',
        )
        assert result is not None
        assert result.engineer_id == "u-zhang"
        assert result.matched_pref is True
        assert unresolved is None

    def test_weak_not_found_keeps_specified_name(self):
        """异常流程：口语指定了但人找不到 → specified_name 留给 tip。"""
        result, unresolved = _run_weak(
            "转给赵不存在", [_complete()],
            '{"has_preference": true, "preferred_name": "赵不存在"}',
            everyone=False,
        )
        assert result is None
        assert unresolved == "赵不存在"

    def test_weak_llm_false_is_unspecified(self):
        """边界：预判误报、LLM 认为没指定 → 不算指定人，无 tip 名。"""
        result, unresolved = _run_weak(
            "给现场看一下日志", [_complete()],
            '{"has_preference": false, "preferred_name": null}',
        )
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
        assert build_redispatch_tip(log, {"u-zhang": "张三"}) == "您指定的接单人画像不完整。"

    def test_pinyin_tip(self):
        """正常流程：拼音命中，对照用户原文和系统找到的人。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-jia",
            preferred_id="u-jia",
            pinyin_match=True,
            name_collision=False,
            profile={"specified_name": "加双", "missing": []},
        )
        assert (
            build_redispatch_tip(log, {"u-jia": "贾爽"})
            == "系统找到的是【贾爽】没有您指定的【加双】，有可能不准确"
        )

    def test_pinyin_tip_without_specified_name(self):
        """正常流程：没有 specified_name 就不从 reasoning 猜原文。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-jia",
            preferred_id="u-jia",
            pinyin_match=True,
            name_collision=False,
            profile={"missing": []},
            reasoning="提单Agent指定接单人: 加双 → 匹配 贾爽（按拼音匹配）",
        )
        assert (
            build_redispatch_tip(log, {"u-jia": "贾爽"})
            == "系统找到的是【贾爽】，有可能不准确"
        )

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
            == "指派人存在同名，已随机选择【张三】；您指定的接单人画像不完整。"
        )

    def test_collision_eval_tip(self):
        """正常流程：同名按评估选定 → 提醒选了谁。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-1",
            preferred_id="u-1",
            pinyin_match=False,
            name_collision=True,
            profile={"missing": [], "collision_random": False},
        )
        assert (
            build_redispatch_tip(log, {"u-1": "张三"})
            == "指派人存在同名，已按评估选择【张三】"
        )

    def test_everyone_fallback_incomplete_tip(self):
        """正常流程：准入池没有此人、全量用户兜底派上 → 画像缺项 tip 在。"""
        result, unresolved = _run_detect(
            "指定处理人：张三", [], everyone=("u-zhang", "张三", False),
        )
        assert result is not None
        assert result.engineer_id == "u-zhang"
        assert (result.profile or {}).get("missing")
        assert unresolved is None
        from app.services.redispatch_tip_service import build_redispatch_tip
        log = SimpleNamespace(
            assigned_id=result.engineer_id,
            preferred_id=result.preferred_id,
            pinyin_match=result.pinyin_match,
            name_collision=result.name_collision,
            profile=result.profile,
        )
        assert build_redispatch_tip(log, {"u-zhang": "张三"}) == "您指定的接单人画像不完整。"


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
