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
            val = everyone
            if len(everyone) == 3 and not isinstance(everyone[0], list):
                uid, uname, py = everyone
                val = ([EngineerProfile(id=uid, name=uname)], py)
            with patch.object(DispatchFlow, "_match_preferred_everyone", return_value=val):
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
                val = everyone_patch
                if len(everyone_patch) == 3 and not isinstance(everyone_patch[0], list):
                    uid, uname, py = everyone_patch
                    val = ([EngineerProfile(id=uid, name=uname)], py)
                with patch.object(DispatchFlow, "_match_preferred_everyone", return_value=val):
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
        assert extract("[指定处理人：张三、李四]") == "张三"
        assert extract("[指定处理人：张三、李四、王五]") == "张三"
        assert extract("车辆定位漂移") is None
        assert extract("转给张三") is None

    def test_parse_multi_flag(self):
        """边界：多人强信号只认第一个，并打 specified_multi。"""
        parse = DispatchFlow._parse_strong_preferred
        assert parse("[指定处理人：张三]") == ("张三", False)
        assert parse("[指定处理人：张三、李四]") == ("张三", True)
        assert parse("指定处理人：张三，李四") == ("张三", True)

    def test_in_description_not_title(self):
        """正常流程：人名写在描述里（提单 Agent 实际落点）也能命中。"""
        result, unresolved = _run_detect(
            "虚拟车任务不执行", [_complete()],
            description="[指定处理人：张三] 任务停留在下发阶段",
        )
        assert result is not None
        assert result.engineer_id == "u-zhang"
        assert unresolved is None

    def test_multi_assigns_first(self):
        """正常流程：[指定处理人：张三、李四] → 派张三，打 specified_multi。"""
        result, unresolved = _run_detect(
            "虚拟车任务不执行",
            [_complete(), _complete("李四", "u-li")],
            description="[指定处理人：张三、李四] 任务停留在下发阶段",
        )
        assert result is not None
        assert result.engineer_id == "u-zhang"
        assert result.preferred_id == "u-zhang"
        assert result.matched_pref is True
        assert (result.profile or {}).get("specified_multi") is True
        assert unresolved is None
        from app.services.redispatch_tip_service import build_redispatch_tip
        log = SimpleNamespace(
            assigned_id=result.engineer_id,
            preferred_id=result.preferred_id,
            pinyin_match=result.pinyin_match,
            name_collision=result.name_collision,
            profile=result.profile,
        )
        assert build_redispatch_tip(log, {"u-zhang": "张三", "u-li": "李四"}) == (
            "工单暂时只允许分配一个处理人"
        )

    def test_multi_skips_to_next(self):
        """正常流程：第一人找不到 → 派第二人，仍打 specified_multi。"""
        result, unresolved = _run_detect(
            "虚拟车任务不执行",
            [_complete("李四", "u-li")],
            everyone=False,
            description="[指定处理人：赵不存在、李四] 任务停留在下发阶段",
        )
        assert result is not None
        assert result.engineer_id == "u-li"
        assert result.preferred_id == "u-li"
        assert (result.profile or {}).get("specified_multi") is True
        assert unresolved is None

    def test_multi_none_found(self):
        """异常流程：名单里的人都找不到 → 记下第一个名字，走智能派单。"""
        result, unresolved = _run_detect(
            "虚拟车任务不执行",
            [_complete()],
            everyone=False,
            description="[指定处理人：赵不存在、钱也没有]",
        )
        assert result is None
        assert unresolved == "赵不存在"


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

    def test_specified_multi_tip(self):
        """正常流程：指定多人且第一人画像不完整 → 两条 tip 叠加。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-zhang",
            preferred_id="u-zhang",
            pinyin_match=False,
            name_collision=False,
            profile={"specified_multi": True, "missing": ["department"]},
        )
        assert build_redispatch_tip(log, {"u-zhang": "张三"}) == (
            "工单暂时只允许分配一个处理人；接单人画像不完整。"
        )

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

    def test_pinyin_and_missing_tip(self):
        """正常流程：拼音对照句叠加画像时不写「您指定的」。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-jia",
            preferred_id="u-jia",
            pinyin_match=True,
            name_collision=False,
            profile={"specified_name": "加双", "missing": ["department"]},
        )
        assert (
            build_redispatch_tip(log, {"u-jia": "贾爽"})
            == "系统找到的是【贾爽】没有您指定的【加双】，有可能不准确；接单人画像不完整。"
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

    def test_redispatch_unmatched_uses_detail(self):
        """正常流程：重派未派到倾向人 → 详情模板（原因+下一步），不用短句。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-li",
            preferred_id="u-zhang",
            pinyin_match=False,
            name_collision=False,
            reasoning="更熟现场模块",
            candidates=[],
            profile={"missing": []},
        )
        tip = build_redispatch_tip(log, {"u-zhang": "张三", "u-li": "李四"})
        assert tip == (
            "很抱歉，未派给您指定的【张三】；"
            "已优先改派给【李四】处理，原因：更熟现场模块。"
            "如需【张三】接单，可 @ 接单人 转派或重新派单。"
        )

    def test_redispatch_unmatched_pref_incomplete(self):
        """正常流程：倾向人画像不全 → 详情里引导补画像，不叠接单人短句。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-li",
            preferred_id="u-zhang",
            pinyin_match=False,
            name_collision=False,
            reasoning="",
            candidates=[{"engineer_id": "u-zhang", "missing": ["department"]}],
            profile={"missing": ["department"]},
        )
        tip = build_redispatch_tip(log, {"u-zhang": "张三", "u-li": "李四"})
        assert "未派给您指定的【张三】" in tip
        assert "已优先改派给【李四】" in tip
        assert "您倾向的【张三】画像不完整（缺：部门）" in tip
        assert "暂未采纳" not in tip
        assert "接单人画像不完整" not in tip

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
            == "指派人存在同名，已随机选择【张三】；接单人画像不完整。"
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

    def test_multi_and_collision_tip(self):
        """正常流程：指定多人且命中同名 → 两条风险都提醒。"""
        from app.services.redispatch_tip_service import build_redispatch_tip

        log = SimpleNamespace(
            assigned_id="u-1",
            preferred_id="u-1",
            pinyin_match=False,
            name_collision=True,
            profile={
                "specified_multi": True,
                "missing": [],
                "collision_random": True,
            },
        )
        assert (
            build_redispatch_tip(log, {"u-1": "张三"})
            == "工单暂时只允许分配一个处理人；指派人存在同名，已随机选择【张三】"
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

    def test_weaker_third_skipped_no_llm(self):
        """正常流程：[0缺,1缺] 只留最完整档一人 → 不调 LLM。"""
        a, b = _complete("张三", "u-1"), _incomplete("张三", "u-2")
        flow = DispatchFlow()
        llm = SimpleNamespace(complete=AsyncMock(return_value='{"selected_id":"u-2"}'))

        async def _go():
            ctx = _ticket("指定处理人：张三")
            with patch("ai.core.get_llm_client", AsyncMock(return_value=llm)):
                return await flow._pick_collision(ctx, "张三", [a, b])

        winner, reason, rnd = asyncio.run(_go())
        assert winner.id == "u-1"
        assert rnd is False
        llm.complete.assert_not_called()

    def test_weaker_third_not_in_llm_or_random(self):
        """正常流程：[0缺,0缺,2缺] 残缺第三人既不进 LLM 也不进随机。"""
        a, b = _complete("张三", "u-1"), _complete("张三", "u-2")
        c = _incomplete("张三", "u-3")
        flow = DispatchFlow()
        llm = SimpleNamespace(complete=AsyncMock(return_value='{"can_determine": false}'))
        captured = {}

        def _choose(seq):
            captured["pool"] = list(seq)
            return seq[0]

        async def _go():
            ctx = _ticket("指定处理人：张三")
            with patch("ai.core.get_llm_client", AsyncMock(return_value=llm)):
                with patch(
                    "ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow.random.choice",
                    side_effect=_choose,
                ):
                    return await flow._pick_collision(ctx, "张三", [a, b, c])

        winner, reason, rnd = asyncio.run(_go())
        assert winner.id == "u-1"
        assert rnd is True
        prompt = llm.complete.await_args.args[0]
        assert "u-3" not in prompt
        assert "u-1" in prompt and "u-2" in prompt
        assert {e.id for e in captured["pool"]} == {"u-1", "u-2"}

    def test_llm_cannot_pick_weaker_third(self):
        """异常流程：模型点名残缺第三人 → 当不在档内，在最完整档随机。"""
        a, b = _complete("张三", "u-1"), _complete("张三", "u-2")
        c = _incomplete("张三", "u-3")
        winner, reason, rnd = _run_pick(
            [a, b, c], '{"selected_id":"u-3","reason":"误选"}', pick=a,
        )
        assert winner.id == "u-1"
        assert rnd is True
        assert winner.id != "u-3"


class TestStep0EveryoneCollision:
    """准入池没有时，全量在职同名也走最完整档，不取表里第一个。"""

    def test_everyone_two_same_name_picks_collision(self):
        """正常流程：池外两个张三 → 同名抉择，打 name_collision。"""
        a = EngineerProfile(id="u-1", name="张三")
        b = EngineerProfile(id="u-2", name="张三")
        flow = DispatchFlow()
        llm = SimpleNamespace(complete=AsyncMock(return_value='{"can_determine": false}'))

        async def _go():
            ctx = _ticket("指定处理人：张三")
            with patch.object(
                DispatchFlow, "_match_preferred_everyone", return_value=([a, b], False),
            ):
                with patch("ai.core.get_llm_client", AsyncMock(return_value=llm)):
                    with patch(
                        "ai.agents.AiDiagnosisPlatform.assigner.pipeline.dispatch_flow.random.choice",
                        return_value=a,
                    ):
                        return await flow._detect_preferred_assignee(ctx, [])

        result, unresolved = asyncio.run(_go())
        assert result is not None
        assert result.engineer_id == "u-1"
        assert result.name_collision is True
        assert result.matched_pref is True
        assert unresolved is None

    def test_everyone_single_no_collision(self):
        """正常流程：池外只有一个张三 → 不打同名。"""
        result, unresolved = _run_detect(
            "指定处理人：张三", [],
            everyone=("u-zhang", "张三", False),
        )
        assert result is not None
        assert result.engineer_id == "u-zhang"
        assert result.name_collision is False


class TestStep0BlocksRedispatch:
    """重派拦截看首轮是否真派上，不看 specified_name。"""

    def test_pinyin_hit_still_blocks(self):
        """边界：拼音命中写了 specified_name，人已派上 → 仍拦截。"""
        from app.services.redispatch_tip_service import step0_blocks_redispatch

        log = SimpleNamespace(
            matched_pref=True,
            preferred_id="u-jia",
            assigned_id="u-jia",
            profile={"specified_name": "加双"},
        )
        assert step0_blocks_redispatch(log) == "u-jia"

    def test_multi_second_person_blocks_with_assignee(self):
        """边界：指定张三、李四实际派了李四 → 拦截 id 是李四。"""
        from app.services.redispatch_tip_service import step0_blocks_redispatch

        log = SimpleNamespace(
            matched_pref=True,
            preferred_id="u-li",
            assigned_id="u-li",
            profile={"specified_multi": True},
        )
        assert step0_blocks_redispatch(log) == "u-li"

    def test_not_found_allows(self):
        """异常流程：找不到指定人走了智能派单 → 放行。"""
        from app.services.redispatch_tip_service import step0_blocks_redispatch

        log = SimpleNamespace(
            matched_pref=None,
            preferred_id=None,
            assigned_id="u-other",
            profile={"specified_name": "赵不存在"},
        )
        assert step0_blocks_redispatch(log) is None

    def test_later_redispatch_log_not_used(self):
        """边界：只认首轮；本函数不看最新一轮表单倾向人。"""
        from app.services.redispatch_tip_service import step0_blocks_redispatch

        first = SimpleNamespace(
            matched_pref=None,
            preferred_id=None,
            assigned_id="u-smart",
            profile={"specified_name": "赵不存在"},
        )
        assert step0_blocks_redispatch(first) is None


class TestStep0LlmJson:
    """弱信号 JSON：中文引号也能解析。"""

    def test_cn_quotes(self):
        """边界：模型用中文引号包 JSON 仍能抽出人名。"""
        data = DispatchFlow._loads_llm_json(
            "好的\n{“has_preference”: true, “preferred_name”: “张三”}"
        )
        assert data is not None
        assert data.get("has_preference") is True
        assert data.get("preferred_name") == "张三"

    def test_plain_json(self):
        """正常流程：标准 JSON。"""
        data = DispatchFlow._loads_llm_json(
            '{"has_preference": true, "preferred_name": "李四"}'
        )
        assert data["preferred_name"] == "李四"


class TestAdmissionJobLevel:
    """智能派单准入：职级也要有。"""

    def test_skip_zero_job_level(self):
        """边界：有部门有模块但职级为 0 → 不进准入池。"""
        from ai.agents.AiDiagnosisPlatform.assigner.sync.engineers_sync import _build_profiles

        rows = [{
            "id": "u-0",
            "name": "零级",
            "department": "智能规划研究院",
            "job_level": 0,
            "responsibility_modules": {"摇人吧服务号": {"前端": ["页面"]}},
        }]
        assert _build_profiles(rows) == []

    def test_keep_job_level_one(self):
        """正常流程：职级 1 且部门模块齐全 → 进池。"""
        from ai.agents.AiDiagnosisPlatform.assigner.sync.engineers_sync import _build_profiles

        rows = [{
            "id": "u-1",
            "name": "一线",
            "department": "智能规划研究院",
            "job_level": 1,
            "responsibility_modules": {"摇人吧服务号": {"前端": ["页面"]}},
        }]
        out = _build_profiles(rows)
        assert len(out) == 1
        assert out[0].id == "u-1"
